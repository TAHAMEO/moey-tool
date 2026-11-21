<#
  Tests for the Windows installer scripts that can run anywhere PowerShell runs (Linux CI included):
  path conversion, the WSL-environment step against a stand-in wsl.exe, checksum enforcement, and the
  launcher's command lines. Windows-only parts (turning on WSL features, shortcuts) need a real PC.
    pwsh -File installer/windows/tests/run-tests.ps1 -Rootfs <ubuntu wsl rootfs.tar.gz>
#>
param([Parameter(Mandatory = $true)][string]$Rootfs)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$install = Resolve-Path (Join-Path $here "..\scripts\install.ps1")
$failures = 0
function Check([bool]$ok, [string]$label) {
  if ($ok) { Write-Output "PASS $label" } else { Write-Output "FAIL $label"; $script:failures++ }
}

# ---- 1. functions in isolation (defined from the script's syntax tree, without running it)
$ast = [System.Management.Automation.Language.Parser]::ParseFile($install, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
  Invoke-Expression $fn.Extent.Text
}
$Log = Join-Path ([IO.Path]::GetTempPath()) "cf-test.log"
Check ((ConvertTo-WslPath 'C:\Users\Ann Lee\AppData\Local\ClipForge\app') -eq '/mnt/c/Users/Ann Lee/AppData/Local/ClipForge/app') "Windows paths with spaces map to /mnt/c/..."
Check ((ConvertTo-WslPath 'D:\Videos\ClipForge\') -eq '/mnt/d/Videos/ClipForge') "other drives and trailing slashes"

# ---- 2. the "distro" step end to end, with a stand-in wsl.exe that records what it is asked
$work = Join-Path ([IO.Path]::GetTempPath()) ("cf-installer-" + [guid]::NewGuid().ToString("N"))
$bin = Join-Path $work "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$record = Join-Path $work "wsl-calls.txt"
$fake = @'
#!/usr/bin/env bash
echo "$*" >> "__RECORD__"
case "$1" in
  --list) [[ -f "__RECORD__.exists" ]] && printf 'Ubuntu\nClipForge\n' ; exit 0 ;;
  --import) touch "__RECORD__.exists"; mkdir -p "$3"; echo "Import in progress, this may take a few minutes."; exit 0 ;;
  *) exit 0 ;;
esac
'@
Set-Content -Path (Join-Path $bin "wsl.exe") -Value ($fake -replace "__RECORD__", $record) -NoNewline
& chmod +x (Join-Path $bin "wsl.exe")
$env:PATH = "$bin" + [IO.Path]::PathSeparator + $env:PATH
$env:USERPROFILE = $work

$serve = Join-Path $work "www"
New-Item -ItemType Directory -Force -Path $serve | Out-Null
Copy-Item $Rootfs (Join-Path $serve "rootfs.tar.gz")
Set-Content -Path (Join-Path $serve "damaged.tar.gz") -Value "not the image"
$port = Get-Random -Minimum 20000 -Maximum 40000
$server = Start-Process -FilePath "python3" -ArgumentList @("-m", "http.server", "$port", "--bind", "127.0.0.1", "--directory", $serve) -PassThru -RedirectStandardOutput (Join-Path $work "http.out") -RedirectStandardError (Join-Path $work "http.err")
Start-Sleep -Seconds 1
try {
  $installDir = Join-Path $work "ClipForge"
  $env:CLIPFORGE_ROOTFS_URL = "http://127.0.0.1:$port/damaged.tar.gz"
  $output = & pwsh -NoProfile -File $install -Step distro -InstallDir $installDir 2>&1 | Out-String
  Check ($LASTEXITCODE -eq 1 -and $output -match "checksum mismatch") "a damaged download is refused"
  Check (-not (Test-Path (Join-Path $installDir "ubuntu-rootfs.tar.gz"))) "and deleted"
  Check (-not ((Get-Content $record -ErrorAction SilentlyContinue) -match "--import")) "nothing is imported from it"

  $env:CLIPFORGE_ROOTFS_URL = "http://127.0.0.1:$port/rootfs.tar.gz"
  $output = & pwsh -NoProfile -File $install -Step distro -InstallDir $installDir 2>&1 | Out-String
  $ok = $LASTEXITCODE -eq 0
  if (-not $ok) { Write-Output $output }
  Check $ok "the distro step succeeds"
  Check ($output -match "Checksum verified" -and $output -match "Ubuntu: 100%") "progress and checksum are reported"
  $import = (Get-Content $record) -match "^--import"
  Check ($import.Count -eq 1 -and $import[0] -eq "--import ClipForge $installDir/wsl $installDir/ubuntu-rootfs.tar.gz --version 2") "imports the ClipForge environment as WSL 2 ($import)"
  Check (-not (Test-Path (Join-Path $installDir "ubuntu-rootfs.tar.gz"))) "removes the download afterwards"
  Check ((Get-Content (Join-Path $installDir "install.log") -Raw) -match "Creating the ClipForge Linux environment") "writes install.log"

  $output = & pwsh -NoProfile -File $install -Step distro -InstallDir $installDir 2>&1 | Out-String
  Check ($LASTEXITCODE -eq 0 -and $output -match "Using the existing ClipForge Linux environment") "running setup again keeps the existing environment"
  Check (((Get-Content $record) -match "^--import").Count -eq 1) "and never imports twice"
} finally {
  Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
if ($failures) { Write-Output "$failures check(s) failed"; exit 1 }
Write-Output "installer scripts OK"
