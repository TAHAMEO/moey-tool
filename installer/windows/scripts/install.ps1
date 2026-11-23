<#
.SYNOPSIS
  Installs or updates CLIPFORGE on Windows 10 and 11 - as an ordinary Windows app: no Docker,
  no Linux, no WSL, no administrator rights.

.DESCRIPTION
  Everything lives in the installation folder:
    app\        CLIPFORGE itself (from setup.exe)       runtime\   Python and its packages
    data\       videos, models and the database          config.env settings and keys for this PC
  Python, PostgreSQL (with pgvector), FFmpeg and Node.js all come as packages pinned by checksum in
  app\backend\uv.lock, installed by uv (itself downloaded from PyPI and checked against its
  SHA-256). setup.exe runs one step at a time so its progress bar moves; every step is safe to run
  again, which is also how updates work.

    preflight  check Windows version, memory and disk space
    previous   stop a running CLIPFORGE (and the older Linux-based version, if there is one)
    runtime    download Python and CLIPFORGE's components (about 1.2 GB)
    setup      create CLIPFORGE's settings and database
    models     download the AI models (the speech model too, about 480 MB, with progress)
    start      start CLIPFORGE and wait until it answers

  By hand:  powershell -ExecutionPolicy Bypass -File install.ps1 -Step all -InstallDir <folder>
  Log:      <folder>\install.log
#>
[CmdletBinding()]
param(
  [ValidateSet("check", "preflight", "previous", "remove-previous", "runtime", "setup", "models", "start", "all")]
  [string]$Step = "all",
  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "ClipForge")
)

# setup.exe is a 32-bit program, and so is the PowerShell it starts. Continue in 64-bit
# PowerShell, so every program found and started is the 64-bit one.
if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) {
  $native = Join-Path $env:WINDIR "Sysnative\WindowsPowerShell\v1.0\powershell.exe"
  & $native -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -Step $Step -InstallDir $InstallDir
  exit $LASTEXITCODE
}

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# uv, the Python package manager, from PyPI (its official Windows build), checked against this SHA-256.
$UvUrl = "https://files.pythonhosted.org/packages/dd/ef/ceeabf3631117f7354787d9fb1ffe943899e59ed4b887a696ed547e47e76/uv-0.13.9-py3-none-win_amd64.whl"
$UvSha256 = "6a44b2ed8111d2f60d2b92627c5dccda3f7ed7570b18ce115e569cd68c28246b"
$UvExeInWheel = "uv-0.13.9.data/scripts/uv.exe"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$InstallDir = (Resolve-Path $InstallDir).Path
$AppDir = Join-Path $InstallDir "app"
$Runtime = Join-Path $InstallDir "runtime"
$Uv = Join-Path $Runtime "uv\uv.exe"
$Venv = Join-Path $Runtime "venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Log = Join-Path $InstallDir "install.log"
$Distro = "ClipForge"  # the older, Linux-based CLIPFORGE (0.2.x) used a WSL environment of this name

# uv keeps Python, its packages and its download cache inside the installation folder.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime "python"
$env:UV_CACHE_DIR = Join-Path $Runtime "cache"
$env:UV_PROJECT_ENVIRONMENT = $Venv
$env:UV_PYTHON_PREFERENCE = "only-managed"
$env:UV_LINK_MODE = "copy"
$env:UV_HTTP_TIMEOUT = "120"
$env:UV_NO_PROGRESS = "1"
$env:PYTHONUTF8 = "1"

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

function Invoke-Native([string]$File, [string[]]$Arguments) {
  # Streams the program's output line by line into the installer window and the log.
  # Windows PowerShell 5.1 turns every line a program writes to stderr into an error record; with
  # "Stop" the first one (Docker's build progress, for example) would abort setup. Not here.
  # "@() |" gives the program an input that has already ended: setup.exe hands this script an input
  # that never ends, and a program asking a question would otherwise wait for an answer forever.
  $previous = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    @() | & $File @Arguments 2>&1 | ForEach-Object {
      $text = ("$_" -replace "`0", "").TrimEnd()
      if ($text) { Say "   $text" }
    }
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previous
  }
}

function ConvertTo-ArgumentLine([string[]]$Arguments) {
  # Quotes arguments the way Windows programs split their command line.
  $quoted = foreach ($argument in $Arguments) {
    if ($argument -ne "" -and $argument -notmatch '[\s"]') { $argument; continue }
    $escaped = $argument -replace '(\\*)"', '$1$1\"'   # backslashes before a quote are doubled, the quote escaped
    $escaped = $escaped -replace '(\\+)$', '$1$1'      # and so are backslashes before the closing quote
    '"' + $escaped + '"'
  }
  return ($quoted -join " ")
}

function Invoke-Limited([string]$File, [string[]]$Arguments, [int]$Seconds, [switch]$Quiet) {
  # For WSL's own commands: an input that has already ended (no question can wait for an answer)
  # and a time limit, so setup never hangs on one. Output is shown once the program has finished.
  # Returns @{ Code; Lines; TimedOut }.
  $info = New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName = $File
  $info.Arguments = ConvertTo-ArgumentLine $Arguments
  $info.UseShellExecute = $false
  $info.CreateNoWindow = $true
  $info.RedirectStandardInput = $true
  $info.RedirectStandardOutput = $true
  $info.RedirectStandardError = $true
  $info.StandardOutputEncoding = [Text.Encoding]::UTF8
  $info.StandardErrorEncoding = [Text.Encoding]::UTF8
  $process = [System.Diagnostics.Process]::Start($info)
  try { $process.StandardInput.Close() } catch { }  # the input ends at once (it may already be gone)
  $reads = @($process.StandardOutput.ReadToEndAsync(), $process.StandardError.ReadToEndAsync())
  $timedOut = -not $process.WaitForExit($Seconds * 1000)
  if ($timedOut) {
    try { $process.Kill() } catch { }
    [void]$process.WaitForExit(10000)
  }
  [void][System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]$reads, 10000)
  $lines = @()
  foreach ($read in $reads) {
    if ($read.Status -ne "RanToCompletion") { continue }  # a program left behind still holds it open
    $lines += @(($read.Result -replace "`0", "") -split "\r?\n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  }
  if (-not $Quiet) { foreach ($line in $lines) { Say "   $line" } }
  if ($timedOut) {
    Say "   ($([IO.Path]::GetFileName($File)) $($Arguments -join ' ') did not finish within $Seconds seconds and was stopped)"
    return @{ Code = 1460; Lines = $lines; TimedOut = $true }
  }
  return @{ Code = $process.ExitCode; Lines = $lines; TimedOut = $false }
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

# --------------------------------------------------------------------------- steps
function Step-Check {
  # How setup runs on this PC (also used by the automated Windows tests).
  Say "Checking how setup runs on this PC"
  Say ("   Windows {0}, PowerShell {1}, 64-bit process: {2}" -f [Environment]::OSVersion.Version, $PSVersionTable.PSVersion,
       [Environment]::Is64BitProcess)
  if ([Environment]::Is64BitOperatingSystem -and -not [Environment]::Is64BitProcess) { Stop-Install "Setup is still running as 32-bit PowerShell." }
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
  # A program that reads its input until it ends (findstr) must not wait: its input has already ended.
  $started = Get-Date
  [void](Invoke-Native "findstr.exe" @("clipforge"))
  $limited = Invoke-Limited "findstr.exe" @("clipforge") 60 -Quiet
  $seconds = [int]((Get-Date) - $started).TotalSeconds
  if ($seconds -gt 30 -or $limited.TimedOut) { Stop-Install "A program waiting for input kept setup waiting for $seconds seconds." }
  Say "   Input check OK ($seconds s)"
  # The time limit stops a program that doesn't finish.
  $started = Get-Date
  $slow = Invoke-Limited "ping.exe" @("-n", "60", "127.0.0.1") 5 -Quiet
  $seconds = [int]((Get-Date) - $started).TotalSeconds
  if (-not $slow.TimedOut -or $seconds -gt 30) { Stop-Install "The time limit did not stop a slow program ($seconds s)." }
  Say "   Time limit check OK ($seconds s)"
  $quoted = ConvertTo-ArgumentLine @("plain", "with space", 'C:\dir with space\', 'say "hi"', "")
  if ($quoted -ne 'plain "with space" "C:\dir with space\\" "say \"hi\"" ""') { Stop-Install "Argument quoting check failed: $quoted" }
  Say "   Argument quoting check OK"
}

function Step-Preflight {
  Say "Checking this PC"
  $os = [Environment]::OSVersion.Version
  if ($os.Major -lt 10 -or $os.Build -lt 17763) {
    Stop-Install "CLIPFORGE needs Windows 10 (version 1809 or later) or Windows 11. This PC has build $($os.Build). Install the Windows updates, then run setup again."
  }
  if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64" -and $env:PROCESSOR_ARCHITEW6432 -ne "AMD64") {
    Stop-Install "CLIPFORGE needs a 64-bit Intel or AMD processor (ARM-based PCs are not supported)."
  }
  $memoryGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
  if ($memoryGb -lt 4) { Stop-Install "CLIPFORGE needs at least 4 GB of memory (this PC has $memoryGb GB)." }
  if ($memoryGb -lt 8) { Say "   Note: $memoryGb GB of memory works for short videos; 8 GB or more is recommended." }
  $drive = (Get-Item $InstallDir).PSDrive
  $freeGb = [math]::Round((Get-PSDrive $drive.Name).Free / 1GB)
  if ($freeGb -lt 10) { Stop-Install "CLIPFORGE needs about 10 GB of free space on drive $($drive.Name): (only $freeGb GB free)." }
  Say "   Windows build $($os.Build), $memoryGb GB memory, $freeGb GB free - OK"
  Say ("   Setup runs in PowerShell {0} ({1})" -f $PSVersionTable.PSVersion, $(if ([Environment]::Is64BitProcess) { "64-bit" } else { "32-bit" }))
}

function Step-Previous {
  # An update: stop the running CLIPFORGE so its files can be replaced.
  if (Test-Path $Python) {
    Say "Stopping the running CLIPFORGE"
    [void](Invoke-Limited $Python @("-m", "clipforge.cli", "desktop", "stop", "--root", $InstallDir) 180 -Quiet)
  }
  # The older version (0.2.x) ran in a Linux environment under WSL: stop it so it gives up port 3000.
  $wsl = Get-WslPath
  $marker = Join-Path $InstallDir "previous-linux-version"
  Remove-Item $marker -Force -ErrorAction SilentlyContinue
  if ($wsl) {
    $list = Invoke-Limited $wsl @("--list", "--quiet") 60 -Quiet
    if ($list.Code -eq 0 -and $list.Lines -contains $Distro) {
      Say "Stopping the older, Linux-based CLIPFORGE"
      Get-CimInstance Win32_Process -Filter "Name = 'wsl.exe'" | Where-Object { $_.CommandLine -like "*clipforge-keepalive*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
      [void](Invoke-Limited $wsl @("--terminate", $Distro) 120 -Quiet)
      Set-Content -Path $marker -Value "found"
    }
  }
  # Continue-after-restart entries left by the older version.
  try { Unregister-ScheduledTask -TaskName "CLIPFORGE setup" -Confirm:$false -ErrorAction Stop } catch { }
  try { Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "ClipForgeSetup" -ErrorAction Stop } catch { }
}

function Step-RemovePrevious {
  # Only when you said yes: delete the older version's Linux environment and its data.
  $wsl = Get-WslPath
  if ($wsl) {
    Say "Removing the older, Linux-based CLIPFORGE and its data"
    [void](Invoke-Limited $wsl @("--unregister", $Distro) 600)
  }
  Remove-Item (Join-Path $InstallDir "wsl") -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item (Join-Path $InstallDir "previous-linux-version") -Force -ErrorAction SilentlyContinue
}

function Install-Uv {
  if (Test-Path $Uv) { return }
  $wheel = Join-Path $InstallDir "uv-download.zip"
  Get-File $UvUrl $wheel "uv (Python package manager)"
  $hash = (Get-FileHash -Algorithm SHA256 -Path $wheel).Hash.ToLower()
  if ($hash -ne $UvSha256) {
    Remove-Item $wheel -Force
    Stop-Install "The uv download is damaged (checksum mismatch). Run setup again."
  }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [IO.Compression.ZipFile]::OpenRead($wheel)
  try {
    $entry = $zip.GetEntry($UvExeInWheel)
    if (-not $entry) { Stop-Install "The uv download doesn't contain uv.exe." }
    New-Item -ItemType Directory -Force -Path (Split-Path $Uv) | Out-Null
    [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $Uv, $true)
  } finally {
    $zip.Dispose()
    Remove-Item $wheel -Force -ErrorAction SilentlyContinue
  }
  Say "   uv checksum verified"
}

function Step-Runtime {
  Say "Downloading Python and CLIPFORGE's components (about 1.2 GB the first time; 5-15 minutes)"
  Install-Uv
  $code = Invoke-Native $Uv @("python", "install", "3.12")
  if ($code -ne 0) { Stop-Install "Python could not be installed (uv exit code $code). Check the internet connection and run setup again." }
  $code = Invoke-Native $Uv @("sync", "--frozen", "--no-dev", "--python", "3.12", "--project", (Join-Path $AppDir "backend"))
  if ($code -ne 0) { Stop-Install "CLIPFORGE's components could not be installed (uv exit code $code). Check the internet connection and run setup again." }
  # The download cache isn't needed once everything is installed (an update downloads what changed).
  Remove-Item $env:UV_CACHE_DIR -Recurse -Force -ErrorAction SilentlyContinue
  Say "   Python and all components are installed"
}

function Invoke-ClipForge([string[]]$Arguments) {
  return Invoke-Native $Python (@("-m", "clipforge.cli", "desktop") + $Arguments + @("--root", $InstallDir))
}

function Step-Setup {
  Say "Creating CLIPFORGE's settings and database"
  $code = Invoke-ClipForge @("init")
  if ($code -ne 0) { Stop-Install "CLIPFORGE's database could not be created (exit code $code). The details are in $InstallDir\logs." }
}

function Step-Models {
  $arguments = @("models")
  if ($env:CLIPFORGE_SKIP_SPEECH_MODEL -ne "1") { $arguments += "--speech" }  # skipped only by the automated tests
  Say "Downloading the AI models (the speech model is about 480 MB; progress below)"
  $code = Invoke-ClipForge $arguments
  if ($code -ne 0) {
    Say "   The models could not all be downloaded right now. CLIPFORGE still works: it downloads the speech model"
    Say "   the first time a video needs it (or run setup again later). (exit code $code)"
  }
}

function Step-Start {
  $launcher = Join-Path $AppDir "installer\windows\scripts\clipforge.ps1"
  $code = Invoke-Native (Join-Path $PSHOME "powershell.exe") @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $launcher, "start", "-NoWindow")
  if ($code -ne 0) { Stop-Install "CLIPFORGE was installed but did not start. The details are in $InstallDir\logs." }
  Say "CLIPFORGE is running at http://localhost:3000"
}

$steps = [ordered]@{ preflight = { Step-Preflight }; previous = { Step-Previous }; runtime = { Step-Runtime };
                     setup = { Step-Setup }; models = { Step-Models }; start = { Step-Start } }
try {
  if ($Step -eq "check") { Step-Check }
  elseif ($Step -eq "remove-previous") { Step-RemovePrevious }
  elseif ($Step -eq "all") { foreach ($name in $steps.Keys) { & $steps[$name] } }
  else { & $steps[$Step] }
} catch {
  Stop-Install $_.Exception.Message
}
exit 0
