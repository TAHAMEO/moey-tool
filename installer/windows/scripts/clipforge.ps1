<#
.SYNOPSIS
  Start, open or stop CLIPFORGE on Windows (the Start menu and desktop shortcuts run this).

    clipforge.ps1 open      start CLIPFORGE if needed (small "Starting..." window), then open it in the browser
    clipforge.ps1 start     start it in the background (used at sign-in when "Start with Windows" is on)
    clipforge.ps1 stop      stop CLIPFORGE (frees the memory it uses)
    clipforge.ps1 status    show whether it is running

  CLIPFORGE runs as ordinary Windows programs (Python, PostgreSQL, Node.js) from its installation
  folder, listening on this PC only (127.0.0.1). No Docker, no Linux.
#>
[CmdletBinding()]
param(
  [ValidateSet("open", "start", "stop", "status")]
  [string]$Action = "open",
  [switch]$NoWindow  # print progress instead of showing a window (used by the installer)
)

# A 32-bit PowerShell (as started by setup.exe) continues as 64-bit.
if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
  $native = Join-Path $env:WINDIR "Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  $forward = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath, $Action)
  if ($NoWindow) { $forward += "-NoWindow" }
  & $native @forward
  exit $LASTEXITCODE
}

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
# This script is in <installation folder>\app\installer\windows\scripts.
$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
$Python = Join-Path $Root "runtime\venv\Scripts\python.exe"
$PythonW = Join-Path $Root "runtime\venv\Scripts\pythonw.exe"
$Log = Join-Path $Root "logs\launcher.log"

function Get-Port {
  $config = Join-Path $Root "config.env"
  if (Test-Path $config) {
    foreach ($line in Get-Content $config) {
      if ($line -match '^\s*CLIPFORGE_WEB_PORT\s*=\s*(\d+)\s*$') { return $Matches[1] }
    }
  }
  return "3000"
}
$Url = "http://localhost:$(Get-Port)"

function Write-Log([string]$Message) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
  Add-Content -Path $Log -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
  if ($NoWindow) { [Console]::Out.WriteLine($line) }
}

function Show-Message([string]$Text, [string]$Icon = "Information") {
  if ($NoWindow) { [Console]::Out.WriteLine($Text); return }
  Add-Type -AssemblyName System.Windows.Forms
  [void][System.Windows.Forms.MessageBox]::Show($Text, "CLIPFORGE", "OK", $Icon)
}

function Invoke-Desktop([string[]]$Arguments) {
  # "@() |": the program's input has already ended, so nothing can wait for an answer.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $lines = @() | & $Python -m clipforge.cli desktop @Arguments --root $Root 2>&1
    foreach ($line in $lines) { Write-Log "   $line" }
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
}

function Test-Ready {
  try { return (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$Url/api/health/ready").StatusCode -eq 200 }
  catch { return $false }
}

function Get-Status {
  $file = Join-Path $Root "run\status.txt"
  if (Test-Path $file) { return (Get-Content $file -Raw).Trim() }
  return ""
}

function Start-ClipForge {
  # pythonw.exe has no window: CLIPFORGE runs in the background until "Stop CLIPFORGE".
  Start-Process -FilePath $PythonW -WindowStyle Hidden -WorkingDirectory $Root `
    -ArgumentList "-m clipforge.cli desktop run --root `"$Root`""
  Write-Log "Starting"
}

function Wait-Ready([int]$Minutes) {
  $deadline = (Get-Date).AddMinutes($Minutes)
  while ((Get-Date) -lt $deadline) {
    if (Test-Ready) { return $true }
    if ((Get-Status) -like "failed*") { return $false }
    Start-Sleep -Seconds 2
  }
  return $false
}

function Show-Starting([int]$Minutes) {
  # A small window while CLIPFORGE starts (usually 10-30 seconds).
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
  $timer.Interval = 1000
  $timer.Add_Tick({
    if (Test-Ready) { $timer.Stop(); $form.Tag = "ready"; $form.Close() }
    elseif ((Get-Status) -like "failed*" -or (Get-Date) -gt $deadline) { $timer.Stop(); $form.Tag = "timeout"; $form.Close() }
  })
  $form.Add_Shown({ $timer.Start() })
  [void]$form.ShowDialog()
  return $form.Tag -eq "ready"
}

if (-not (Test-Path $PythonW)) {
  Show-Message "CLIPFORGE isn't completely installed. Run the CLIPFORGE setup again to repair it." "Warning"
  exit 1
}

switch ($Action) {
  "status" {
    [void](Invoke-Desktop @("status"))
    if (Test-Ready) { Write-Output "CLIPFORGE is running at $Url" } else { Write-Output "CLIPFORGE is not running ($(Get-Status))" }
  }
  "stop" {
    Write-Log "Stopping"
    $code = Invoke-Desktop @("stop")
    if ($code -ne 0) { Show-Message "CLIPFORGE didn't stop in time. The log is in $Root\logs." "Warning"; exit 1 }
    Show-Message "CLIPFORGE is stopped. Scheduled Instagram posts wait until you start it again; YouTube posts handed to YouTube still go out."
  }
  default {
    if (Test-Ready) {
      if ($Action -eq "open") { Start-Process $Url }
      exit 0
    }
    Start-ClipForge
    $ready = if ($NoWindow -or $Action -eq "start") { Wait-Ready 5 } else { Show-Starting 5 }
    if (-not $ready) {
      $status = Get-Status
      Write-Log "Did not become ready ($status)"
      Show-Message "CLIPFORGE didn't start ($status). Try again in a minute; if it keeps happening, the logs are in $Root\logs" "Warning"
      exit 1
    }
    Write-Log "Ready at $Url"
    if ($Action -eq "open") { Start-Process $Url }
  }
}
exit 0
