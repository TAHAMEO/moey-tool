<#
.SYNOPSIS
  Installs or updates CLIPFORGE on Windows 10 (version 2004 or later) and Windows 11.

.DESCRIPTION
  CLIPFORGE runs in its own WSL 2 Linux environment called "ClipForge", with the free Docker
  Engine inside it (Docker Desktop is not needed). setup.exe calls this script one step at a time
  so its progress bar advances; every step is safe to run again, which is also how updates work.

    preflight  check Windows version, 64-bit, memory, disk space and virtualization
    wsl        turn on WSL 2 if needed (exit code 3010 = restart Windows, then setup continues)
    distro     download Ubuntu 24.04 for WSL (checksum verified) and create the ClipForge environment
    system     install Docker Engine inside it and enable systemd
    app        copy CLIPFORGE in, create Videos\ClipForge, build and start it
    models     download the speech and vision models (verified)
    start      start CLIPFORGE and wait until it answers

  By hand:  powershell -ExecutionPolicy Bypass -File install.ps1 -Step all
  Log:      %LOCALAPPDATA%\ClipForge\install.log
#>
[CmdletBinding()]
param(
  [ValidateSet("check", "preflight", "wsl", "distro", "system", "app", "models", "start", "all")]
  [string]$Step = "all",
  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "ClipForge")
)

# setup.exe is a 32-bit program, and so is the PowerShell it starts. On 64-bit Windows a 32-bit
# process is shown C:\Windows\SysWOW64 when it asks for System32 - and wsl.exe exists only in the
# real System32. So always continue in 64-bit PowerShell (the "Sysnative" path reaches it).
if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
  $native = Join-Path $env:WINDIR "Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  & $native -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -Step $Step -InstallDir $InstallDir
  exit $LASTEXITCODE
}

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$env:WSL_UTF8 = "1"  # wsl.exe prints UTF-8 instead of UTF-16 when its output is captured

$Distro = "ClipForge"
$RootfsUrl = "https://cloud-images.ubuntu.com/wsl/releases/24.04/20240423/ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz"
$RootfsSha256 = "8251e27ffff381a4af5f41dcb94d867de3e0d9774a9241908ab34555d99315ea"
# Offline or company networks: point CLIPFORGE_ROOTFS_URL at a copy of the same file (the checksum still applies,
# unless CLIPFORGE_ROOTFS_SHA256 names the checksum of a different image you trust).
if ($env:CLIPFORGE_ROOTFS_URL) { $RootfsUrl = $env:CLIPFORGE_ROOTFS_URL }
if ($env:CLIPFORGE_ROOTFS_SHA256) { $RootfsSha256 = $env:CLIPFORGE_ROOTFS_SHA256.ToLower() }
$RebootRequired = 3010
$AppDir = Join-Path $InstallDir "app"
$Log = Join-Path $InstallDir "install.log"
$Videos = [Environment]::GetFolderPath("MyVideos")  # follows a Videos folder moved to another drive or OneDrive
if (-not $Videos) { $Videos = Join-Path $HOME "Videos" }
$Inbox = Join-Path $Videos "ClipForge"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

function Say([string]$Message) {
  # Straight to the console (setup.exe shows it), never into a function's return value.
  $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
  [Console]::Out.WriteLine($line)
  [Console]::Out.Flush()
  Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Stop-Install([string]$Message) {
  Say "ERROR: $Message"
  Say "The full log is in $Log"
  exit 1
}

function ConvertTo-WslPath([string]$WindowsPath) {
  # C:\Users\Me\Videos -> /mnt/c/Users/Me/Videos (how Windows drives appear inside WSL)
  $full = if ($WindowsPath -match '^[A-Za-z]:\\') { $WindowsPath } else { [IO.Path]::GetFullPath($WindowsPath) }
  if ($full -notmatch '^([A-Za-z]):\\(.*)$') { Stop-Install "Unsupported folder: $full" }
  return ("/mnt/" + $Matches[1].ToLower() + "/" + ($Matches[2] -replace '\\', '/')).TrimEnd('/')
}

function Invoke-Native([string]$File, [string[]]$Arguments) {
  # Streams the program's output line by line into the installer window and the log.
  # Windows PowerShell 5.1 turns every line a program writes to stderr into an error record; with
  # "Stop" the first one (Docker's build progress, for example) would abort setup. Not here.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $File @Arguments 2>&1 | ForEach-Object {
      $text = ("$_" -replace "`0", "").TrimEnd()
      if ($text) { Say "   $text" }
    }
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
}

function Invoke-Quiet([string]$File, [string[]]$Arguments) {
  # Runs a program for its exit code and output lines only (nothing is shown).
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $lines = & $File @Arguments 2>$null
    return @{ Code = $LASTEXITCODE; Lines = @($lines | ForEach-Object { ("$_" -replace "`0", "").Trim() }) }
  } finally {
    $ErrorActionPreference = $previous
  }
}

function Get-WslPath {
  # The real System32 copy (inbox launcher), then the Store/MSI install, then anything on PATH.
  $candidates = @()
  if ($env:WINDIR) { $candidates += Join-Path $env:WINDIR "System32\wsl.exe" }
  $programFiles = if ($env:ProgramW6432) { $env:ProgramW6432 } else { $env:ProgramFiles }
  if ($programFiles) { $candidates += Join-Path $programFiles "WSL\wsl.exe" }
  foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate) { return $candidate } }
  $command = Get-Command "wsl.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($command) { return $command.Source }
  return $null
}

function Get-Wsl {
  $wsl = Get-WslPath
  if (-not $wsl) { Stop-Install "WSL is not installed yet. Restart Windows, then run CLIPFORGE setup again." }
  return $wsl
}

function Invoke-Wsl([string[]]$Arguments) {
  $wsl = Get-WslPath
  if (-not $wsl) { return 9009 }  # like "command not found"
  return Invoke-Native $wsl $Arguments
}

function Test-DistroExists {
  $wsl = Get-WslPath
  if (-not $wsl) { return $false }
  $result = Invoke-Quiet $wsl @("--list", "--quiet")
  if ($result.Code -ne 0) { return $false }
  return ($result.Lines -contains $Distro)
}

function Invoke-InDistro([string[]]$Arguments) {
  # --exec hands every argument to Linux exactly as given; "--" would let a shell reinterpret
  # characters such as \ ' $ in Windows folder names.
  return Invoke-Native (Get-Wsl) (@("--distribution", $Distro, "--user", "root", "--exec") + $Arguments)
}

function Get-File([string]$Url, [string]$Destination, [string]$Label) {
  Add-Type -AssemblyName System.Net.Http
  $client = New-Object System.Net.Http.HttpClient
  $client.Timeout = [TimeSpan]::FromMinutes(60)
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
      $response = $client.GetAsync($Url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).Result
      if (-not $response.IsSuccessStatusCode) { throw "HTTP $([int]$response.StatusCode)" }
      $total = $response.Content.Headers.ContentLength
      $in = $response.Content.ReadAsStreamAsync().Result
      $out = [IO.File]::Create($Destination)
      try {
        $buffer = New-Object byte[] (1MB)
        $done = 0; $lastShown = -10
        while (($read = $in.Read($buffer, 0, $buffer.Length)) -gt 0) {
          $out.Write($buffer, 0, $read)
          $done += $read
          if ($total) {
            $percent = [int](100 * $done / $total)
            if ($percent -ge $lastShown + 10) {
              Say ("   {0}: {1}% ({2:N0} of {3:N0} MB)" -f $Label, $percent, ($done / 1MB), ($total / 1MB))
              $lastShown = $percent
            }
          }
        }
      } finally { $out.Close(); $in.Close() }
      return
    } catch {
      Say "   Download attempt $attempt failed: $($_.Exception.Message)"
      if ($attempt -eq 3) { Stop-Install "Could not download $Label. Check the internet connection and run setup again." }
      Start-Sleep -Seconds (5 * $attempt)
    }
  }
}

$ResumeTask = "CLIPFORGE setup"
$RunOnceKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce"

function Register-Resume {
  # setup.exe needs administrator rights, and Windows may skip a program in the RunOnce key that
  # asks for them at sign-in. A sign-in task with the highest privileges starts it without asking.
  $setup = Join-Path $InstallDir "setup.exe"
  if (-not (Test-Path -LiteralPath $setup)) { Say "   After the restart, run CLIPFORGE setup again."; return }
  try {
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute $setup -Argument "/resume"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = "PT20S"
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Priority 5
    Register-ScheduledTask -TaskName $ResumeTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Say "   Setup will continue by itself after you sign in again"
  } catch {
    Say "   Note: could not create the sign-in task ($($_.Exception.Message)); using RunOnce instead"
    try { Set-ItemProperty -Path $RunOnceKey -Name "ClipForgeSetup" -Value "`"$setup`" /resume" }
    catch { Say "   After the restart, run CLIPFORGE setup again." }
  }
}

function Unregister-Resume {
  # Nothing to remove is the usual case; no error here may stop setup.
  try { Unregister-ScheduledTask -TaskName $ResumeTask -Confirm:$false -ErrorAction Stop } catch { }
  try { Remove-ItemProperty -Path $RunOnceKey -Name "ClipForgeSetup" -ErrorAction Stop } catch { }
}

function Request-Restart([string]$Message) {
  Set-Content -Path (Join-Path $InstallDir "wsl-restart-requested") -Value (Get-Date -Format o)
  Register-Resume
  Say $Message
  exit $RebootRequired
}

# --------------------------------------------------------------------------- steps
function Step-Check {
  # How setup runs on this PC (also used by the automated Windows tests).
  Say "Checking how setup runs on this PC"
  Say ("   Windows {0}, PowerShell {1}, 64-bit process: {2}" -f [Environment]::OSVersion.Version, $PSVersionTable.PSVersion,
       [Environment]::Is64BitProcess)
  if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) { Stop-Install "Setup is still running as 32-bit PowerShell." }
  $wsl = Get-WslPath
  if ($wsl) { Say "   wsl.exe: $wsl" } else { Say "   wsl.exe: not found yet (the WSL step installs it)" }
  $code = Invoke-Native "cmd.exe" @("/c", "echo output-check-stdout & echo output-check-stderr 1>&2 & exit /b 3")
  if ($code -ne 3) { Stop-Install "Program output check failed (exit code $code instead of 3)." }
  Say "   Program output check OK"
  # The launcher starts CLIPFORGE's Linux environment as a background program while setup reads
  # its output; that must not keep setup waiting until the background program ends.
  $pidFile = Join-Path $InstallDir "check-background.pid"
  $command = "`$p = Start-Process -FilePath ping.exe -ArgumentList '-n 120 127.0.0.1' -WindowStyle Hidden -PassThru; Set-Content -Path '$($pidFile -replace "'", "''")' -Value `$p.Id"
  $started = Get-Date
  [void](Invoke-Native (Join-Path $PSHOME "powershell.exe") @("-NoProfile", "-Command", $command))
  $seconds = [int]((Get-Date) - $started).TotalSeconds
  if (Test-Path $pidFile) {
    Stop-Process -Id ([int](Get-Content $pidFile)) -Force -ErrorAction SilentlyContinue
    Remove-Item $pidFile -Force
  }
  if ($seconds -gt 60) { Stop-Install "A program started in the background kept setup waiting for $seconds seconds." }
  Say "   Background start check OK ($seconds s)"
}

function Step-Preflight {
  Say "Checking this PC"
  Unregister-Resume  # this run is the one a restart was waiting for
  $os = [Environment]::OSVersion.Version
  if ($os.Major -lt 10 -or $os.Build -lt 19041) {
    Stop-Install "CLIPFORGE needs Windows 10 version 2004 (build 19041) or later, or Windows 11. This PC has build $($os.Build). Install the Windows updates, then run setup again."
  }
  if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64" -and $env:PROCESSOR_ARCHITEW6432 -ne "AMD64") {
    Stop-Install "CLIPFORGE needs a 64-bit Intel or AMD processor (ARM-based PCs are not supported)."
  }
  $memoryGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
  if ($memoryGb -lt 8) { Stop-Install "CLIPFORGE needs at least 8 GB of memory (this PC has $memoryGb GB)." }
  if ($memoryGb -lt 12) { Say "   Note: $memoryGb GB of memory works, but 16 GB is recommended." }
  $drive = (Get-Item $InstallDir).PSDrive
  $freeGb = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB)
  if ($freeGb -lt 25) { Stop-Install "CLIPFORGE needs about 25 GB of free space on drive $($drive.Name): (only $freeGb GB free)." }
  $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
  $hypervisor = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent
  if (-not $cpu.VirtualizationFirmwareEnabled -and -not $hypervisor) {
    Say "   Warning: hardware virtualization looks switched off. If WSL 2 fails to start, turn on 'Intel VT-x' / 'AMD-V' (SVM) in the PC's BIOS/UEFI settings."
  }
  Say "   Windows build $($os.Build), $memoryGb GB memory, $freeGb GB free - OK"
  $wsl = Get-WslPath
  $wslText = if ($wsl) { $wsl } else { "not installed yet" }
  Say ("   Setup runs in PowerShell {0} ({1}); wsl.exe: {2}" -f $PSVersionTable.PSVersion,
       $(if ([Environment]::Is64BitProcess) { "64-bit" } else { "32-bit" }), $wslText)
}

function Test-ModernWsl {
  # The current WSL (from the Store or its MSI) answers --version; the old built-in one of early Windows 10 doesn't.
  $wsl = Get-WslPath
  if (-not $wsl) { return $false }
  return (Invoke-Quiet $wsl @("--version")).Code -eq 0
}

function Install-WslMsi {
  # Fallback when "wsl --install/--update" isn't available (older Windows 10): the official WSL package.
  try {
    $release = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/microsoft/WSL/releases/latest" -Headers @{ "User-Agent" = "clipforge-setup" }
    $asset = $release.assets | Where-Object { $_.name -like "*x64.msi" } | Select-Object -First 1
    if (-not $asset) { return $false }
    $msi = Join-Path $InstallDir $asset.name
    Get-File $asset.browser_download_url $msi "WSL $($release.tag_name)"
    $signature = Get-AuthenticodeSignature $msi
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Microsoft Corporation") {
      Remove-Item $msi -Force
      Say "   The downloaded WSL package is not signed by Microsoft; not installing it."
      return $false
    }
    $process = Start-Process msiexec.exe -ArgumentList @("/i", "`"$msi`"", "/quiet", "/norestart") -Wait -PassThru
    Remove-Item $msi -Force -ErrorAction SilentlyContinue
    return ($process.ExitCode -eq 0 -or $process.ExitCode -eq 3010)
  } catch {
    Say "   Could not install the WSL package: $($_.Exception.Message)"
    return $false
  }
}

function Step-Wsl {
  Say "Checking WSL 2 (Windows Subsystem for Linux)"
  $marker = Join-Path $InstallDir "wsl-restart-requested"
  $restart = $false
  foreach ($feature in @("Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform")) {
    $state = "$((Get-WindowsOptionalFeature -Online -FeatureName $feature).State)"
    if ($state -eq "Enabled") { continue }
    if ($state -eq "EnablePending") {
      Say "   The Windows feature $feature is turned on and waits for a restart"
      $restart = $true
      continue
    }
    Say "   Turning on the Windows feature $feature"
    $result = Enable-WindowsOptionalFeature -Online -FeatureName $feature -All -NoRestart -WarningAction SilentlyContinue
    if ($result.RestartNeeded) { $restart = $true }
  }
  if (-not (Test-ModernWsl)) {
    Say "   Installing the current WSL release"
    [void](Invoke-Wsl @("--install", "--no-distribution", "--web-download"))
    if (-not (Test-ModernWsl)) { [void](Invoke-Wsl @("--update", "--web-download")) }
    if (-not (Test-ModernWsl)) { [void](Install-WslMsi) }
  } else {
    [void](Invoke-Wsl @("--update", "--web-download"))
  }
  if ($restart) {
    Request-Restart "Windows needs to restart to finish turning on WSL 2. Setup continues automatically after you sign in again."
  }
  if (-not (Test-ModernWsl)) {
    if (Test-Path $marker) {
      # Already restarted once for this: don't ask again in a loop, explain what to do instead.
      Remove-Item $marker -Force
      Stop-Install ("WSL could not be installed automatically. Install 'Windows Subsystem for Linux' from the Microsoft Store " +
                    "(or run Windows Update), restart, then run CLIPFORGE setup again.")
    }
    Request-Restart "WSL is not ready yet; a restart usually finishes its installation."
  }
  Remove-Item $marker -Force -ErrorAction SilentlyContinue
  [void](Invoke-Wsl @("--set-default-version", "2"))
  Say "   WSL 2 is ready"
}

function Step-Distro {
  if (Test-DistroExists) {
    Say "Using the existing ClipForge Linux environment (your data is kept)"
    return
  }
  Say "Downloading Ubuntu 24.04 for WSL (about 340 MB)"
  $tarball = Join-Path $InstallDir "ubuntu-rootfs.tar.gz"
  Get-File $RootfsUrl $tarball "Ubuntu"
  $hash = (Get-FileHash -Algorithm SHA256 -Path $tarball).Hash.ToLower()
  if ($hash -ne $RootfsSha256) {
    Remove-Item $tarball -Force
    Stop-Install "The Ubuntu download is damaged (checksum mismatch). Run setup again."
  }
  Say "   Checksum verified"
  Say "Creating the ClipForge Linux environment"
  $target = Join-Path $InstallDir "wsl"
  New-Item -ItemType Directory -Force -Path $target | Out-Null
  $code = Invoke-Native (Get-Wsl) @("--import", $Distro, $target, $tarball, "--version", "2")
  Remove-Item $tarball -Force -ErrorAction SilentlyContinue
  if ($code -ne 0) { Stop-Install "WSL could not create the Linux environment (wsl --import exit code $code). If virtualization is off in the BIOS/UEFI, turn it on." }
  Configure-WslMemory
}

function Configure-WslMemory {
  # WSL 2 may use half the PC's memory by default; the speech model needs ~5.5 GB. Only an absent
  # .wslconfig is created, so settings made for other Linux environments are never changed.
  try {
    $config = Join-Path $env:USERPROFILE ".wslconfig"
    if (Test-Path $config) { return }
    $memoryGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
    $give = [math]::Max(6, [math]::Min($memoryGb - 4, [math]::Round($memoryGb * 0.7)))
    Set-Content -Path $config -Encoding ASCII -Value "[wsl2]`r`n# Set by the CLIPFORGE installer: memory for Linux environments (CLIPFORGE needs about 6 GB).`r`nmemory=${give}GB`r`n"
    Say "   Gave WSL 2 up to $give GB of memory ($config)"
  } catch {
    Say "   Note: could not adjust WSL 2 memory ($($_.Exception.Message)); the default is used."
  }
}

function Step-System {
  $script = ConvertTo-WslPath (Join-Path $AppDir "installer\windows\scripts\provision.sh")
  $code = Invoke-InDistro @("bash", $script, "system")
  if ($code -ne 0) { Stop-Install "Installing Docker Engine failed (exit code $code)." }
  # Restart the environment so systemd (and with it Docker) starts automatically from now on.
  [void](Invoke-Quiet (Get-Wsl) @("--terminate", $Distro))
}

function Step-App {
  New-Item -ItemType Directory -Force -Path $Inbox | Out-Null
  $readme = Join-Path $Inbox "README.txt"
  if (-not (Test-Path $readme)) {
    Set-Content -Path $readme -Encoding UTF8 -Value @"
CLIPFORGE inbox
===============
Make a folder here for each kind of video (for example "Podcast"), then in CLIPFORGE open
My Videos > Process a folder > Watch a CLIPFORGE folder and choose it. Every video you put in
that folder is then turned into clips automatically, with the settings you chose for it.
"@
  }
  $script = ConvertTo-WslPath (Join-Path $AppDir "installer\windows\scripts\provision.sh")
  $code = Invoke-InDistro @("bash", $script, "app", (ConvertTo-WslPath $AppDir), (ConvertTo-WslPath $Inbox), $Inbox)
  if ($code -ne 0) { Stop-Install "Building CLIPFORGE failed (exit code $code). Running setup again resumes where it stopped." }
}

function Step-Models {
  $code = Invoke-InDistro @("bash", "/opt/clipforge/installer/windows/scripts/provision.sh", "models")
  if ($code -ne 0) {
    Say "   The models could not be downloaded right now. CLIPFORGE works without them except for speech recognition;"
    Say "   Settings shows how to retry. (exit code $code)"
  }
}

function Step-Start {
  $launcher = Join-Path $AppDir "installer\windows\scripts\clipforge.ps1"
  $code = Invoke-Native (Join-Path $PSHOME "powershell.exe") @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher, "start", "-NoWindow")
  if ($code -ne 0) { Stop-Install "CLIPFORGE was installed but did not start. Use the CLIPFORGE shortcut to try again." }
  Say "CLIPFORGE is running at http://localhost:3000"
}

$steps = [ordered]@{ preflight = { Step-Preflight }; wsl = { Step-Wsl }; distro = { Step-Distro }; system = { Step-System };
                     app = { Step-App }; models = { Step-Models }; start = { Step-Start } }
try {
  if ($Step -eq "check") { Step-Check }
  elseif ($Step -eq "all") { foreach ($name in $steps.Keys) { & $steps[$name] } }
  else { & $steps[$Step] }
} catch {
  Stop-Install $_.Exception.Message
}
exit 0
