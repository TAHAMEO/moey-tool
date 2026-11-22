<#
  Tests for the Windows installer's helper functions that run anywhere PowerShell runs (Linux CI
  included): argument quoting, programs that wait for input, the time limit, and the uv checksum
  pin. The full installation is tested on real Windows machines (.github/workflows/windows-installer.yml).
    pwsh -File installer/windows/tests/run-tests.ps1
#>
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$install = Resolve-Path (Join-Path $here "..\scripts\install.ps1")
$failures = 0
function Check([bool]$ok, [string]$label) {
  if ($ok) { Write-Output "PASS $label" } else { Write-Output "FAIL $label"; $script:failures++ }
}

# Functions in isolation (defined from the script's syntax tree, without running it).
$ast = [System.Management.Automation.Language.Parser]::ParseFile($install, [ref]$null, [ref]$null)
foreach ($fn in $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
  Invoke-Expression $fn.Extent.Text
}
$work = Join-Path ([IO.Path]::GetTempPath()) ("cf-installer-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$Log = Join-Path $work "test.log"

try {
  $line = ConvertTo-ArgumentLine @("plain", "with space", 'C:\dir with space\', 'say "hi"', "")
  Check ($line -eq 'plain "with space" "C:\dir with space\\" "say \"hi\"" ""') "arguments are quoted the way Windows programs split them"

  $echo = Invoke-Limited "printf" @('%s|', "with space", 'quote "x"', 'back\slash\') 20 -Quiet
  Check (($echo.Lines -join "") -eq 'with space|quote "x"|back\slash\|') "and arrive unchanged"

  $started = Get-Date
  $slow = Invoke-Limited "sleep" @("30") 3 -Quiet
  Check ($slow.TimedOut -and ((Get-Date) - $started).TotalSeconds -lt 15) "a program that doesn't finish is stopped at its time limit"

  $reader = Invoke-Limited "cat" @() 20 -Quiet
  Check (-not $reader.TimedOut -and $reader.Code -eq 0) "a program reading its input gets an input that has already ended"

  $both = Invoke-Limited "bash" @("-c", 'echo out; echo err >&2; exit 4') 20 -Quiet
  Check ($both.Code -eq 4 -and ($both.Lines -contains "out") -and ($both.Lines -contains "err")) "output, errors and the exit code are captured"

  $source = Get-Content -Raw $install
  Check ($source -match '\$UvSha256 = "[0-9a-f]{64}"' -and $source -match 'files\.pythonhosted\.org/.+/uv-[0-9.]+-py3-none-win_amd64\.whl') "uv comes from PyPI, pinned by SHA-256"
  Check ($source -match '"sync", "--frozen"') "packages are installed exactly as locked (uv.lock checksums)"
  Check ($source -notmatch 'RunAs|Enable-WindowsOptionalFeature') "nothing needs administrator rights"
} finally {
  Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
if ($failures) { Write-Output "$failures check(s) failed"; exit 1 }
Write-Output "installer scripts OK"
