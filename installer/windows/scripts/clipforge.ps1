<#
.SYNOPSIS
  Start, open or stop CLIPFORGE on Windows (the Start menu and desktop shortcuts run this).

    clipforge.ps1 open      start CLIPFORGE if needed (small "Starting..." window), then open it in the browser
    clipforge.ps1 start     start it in the background (used at sign-in when "Start with Windows" is on)
    clipforge.ps1 stop      stop CLIPFORGE and its Linux environment (frees the memory it uses)
    clipforge.ps1 status    show what is running
#>
[CmdletBinding()]
param(
  [ValidateSet("open", "start", "stop", "status")]
  [string]$Action = "open",
  [switch]$NoWindow  # print progress instead of showing a window (used by the installer)
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:WSL_UTF8 = "1"
$Distro = "ClipForge"
$Url = "http://localhost:3000"
$Provision = "/opt/clipforge/installer/windows/scripts/provision.sh"
$Log = Join-Path $env:LOCALAPPDATA "ClipForge\clipforge.log"

function Write-Log([string]$Message) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  Add-Content -Path $Log -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
  if ($NoWindow) { [Console]::Out.WriteLine($line) }
}

function Show-Message([string]$Text, [string]$Icon = "Information") {
  if ($NoWindow) { [Console]::Out.WriteLine($Text); return }
  Add-Type -AssemblyName System.Windows.Forms
  [void][System.Windows.Forms.MessageBox]::Show($Text, "CLIPFORGE", "OK", $Icon)
}

function Test-Installed {
  $names = & wsl.exe --list --quiet 2>$null
  foreach ($name in $names) { if ((("$name" -replace "`0", "").Trim()) -eq $Distro) { return $true } }
  return $false
}

function Test-Ready {
  try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$Url/api/health/ready").StatusCode -eq 200 }
  catch { return $false }
}

function Get-KeepAlive {
  Get-CimInstance Win32_Process -Filter "Name = 'wsl.exe'" | Where-Object { $_.CommandLine -like "*clipforge-keepalive*" }
}

function Start-ClipForge {
  # WSL stops a Linux environment once nothing uses it; this hidden process keeps CLIPFORGE's running.
  if (-not (Get-KeepAlive)) {
    Start-Process -FilePath "wsl.exe" -WindowStyle Hidden -ArgumentList @(
      "--distribution", $Distro, "--user", "root", "--", "bash", "-c", "exec -a clipforge-keepalive sleep infinity")
    Write-Log "Started the Linux environment"
  }
  return Start-Process -FilePath "wsl.exe" -WindowStyle Hidden -PassThru -ArgumentList @(
    "--distribution", $Distro, "--user", "root", "--", "bash", $Provision, "start")
}

function Wait-Ready([int]$Minutes) {
  $deadline = (Get-Date).AddMinutes($Minutes)
  while ((Get-Date) -lt $deadline) {
    if (Test-Ready) { return $true }
    Start-Sleep -Seconds 2
  }
  return $false
}

function Show-Starting([int]$Minutes) {
  # A small window while the containers start (usually 20-60 seconds).
  Add-Type -AssemblyName System.Windows.Forms, System.Drawing
  $form = New-Object System.Windows.Forms.Form
  $form.Text = "CLIPFORGE"
  $form.Size = New-Object System.Drawing.Size(400, 150)
  $form.FormBorderStyle = "FixedDialog"
  $form.StartPosition = "CenterScreen"
  $form.MaximizeBox = $false
  $form.MinimizeBox = $true
  $label = New-Object System.Windows.Forms.Label
  $label.Text = "Starting CLIPFORGE...  This takes up to a minute."
  $label.AutoSize = $true
  $label.Location = New-Object System.Drawing.Point(20, 22)
  $bar = New-Object System.Windows.Forms.ProgressBar
  $bar.Style = "Marquee"
  $bar.MarqueeAnimationSpeed = 30
  $bar.Size = New-Object System.Drawing.Size(345, 18)
  $bar.Location = New-Object System.Drawing.Point(20, 58)
  $form.Controls.AddRange(@($label, $bar))
  $deadline = (Get-Date).AddMinutes($Minutes)
  $timer = New-Object System.Windows.Forms.Timer
  $timer.Interval = 2000
  $timer.Add_Tick({
    if (Test-Ready) { $timer.Stop(); $form.Tag = "ready"; $form.Close() }
    elseif ((Get-Date) -gt $deadline) { $timer.Stop(); $form.Tag = "timeout"; $form.Close() }
  })
  $form.Add_Shown({ $timer.Start() })
  [void]$form.ShowDialog()
  return $form.Tag -eq "ready"
}

if (-not (Test-Installed)) {
  Show-Message "CLIPFORGE's Linux environment is missing. Run the CLIPFORGE setup again to repair it." "Warning"
  exit 1
}

switch ($Action) {
  "status" {
    & wsl.exe --distribution $Distro --user root -- bash $Provision status
    if (Test-Ready) { Write-Output "CLIPFORGE is running at $Url" } else { Write-Output "CLIPFORGE is not answering at $Url" }
  }
  "stop" {
    Write-Log "Stopping"
    & wsl.exe --distribution $Distro --user root -- bash $Provision stop 2>&1 | Out-Null
    Get-KeepAlive | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    & wsl.exe --terminate $Distro 2>&1 | Out-Null
    Show-Message "CLIPFORGE is stopped. Scheduled Instagram posts wait until you start it again; YouTube posts handed to YouTube still go out."
  }
  default {
    if (Test-Ready) {
      if ($Action -eq "open") { Start-Process $Url }
      exit 0
    }
    Write-Log "Starting"
    [void](Start-ClipForge)
    $ready = if ($NoWindow -or $Action -eq "start") { Wait-Ready 10 } else { Show-Starting 5 }
    if (-not $ready) {
      Write-Log "Did not become ready in time"
      Show-Message "CLIPFORGE didn't start in time. Try again in a minute; if it keeps happening, the log is in $Log" "Warning"
      exit 1
    }
    Write-Log "Ready at $Url"
    if ($Action -eq "open") { Start-Process $Url }
  }
}
exit 0
