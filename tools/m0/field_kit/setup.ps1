<#
SAE M0 field kit bootstrap. Per-user only, no admin rights.

Run:  powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 [-DryRun]

SHA256SUMS checks integrity (a corrupt or partial download), not authenticity.
This script never writes under 'Paradox Interactive', the Stellaris install or the mod folder,
and never launches Stellaris.
#>
param([switch]$DryRun)
Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$Kit = $PSScriptRoot
$UvVersion = '0.13.1'
$UvUrl = 'https://github.com/astral-sh/uv/releases/download/0.13.1/uv-x86_64-pc-windows-msvc.zip'
$UvSha256 = '3b2661a463a5baea7f5076ea2b25ab71d77f36a6c0d809e5350651792f80a672'
$UvRoot = Join-Path $env:LOCALAPPDATA 'sae-kit\uv'

function Say($m) { Write-Host $m }
function Plan($m) { Write-Host "DRY-RUN would: $m" }

# (1) Integrity check
$sums = Join-Path $Kit 'SHA256SUMS'
if (-not (Test-Path -LiteralPath $sums)) { Say 'SHA256SUMS missing; re-download the kit.'; exit 1 }
foreach ($line in Get-Content -LiteralPath $sums) {
    if ($line.Trim() -eq '') { continue }
    $parts = $line -split '  ', 2
    $file = Join-Path $Kit ($parts[1] -replace '/', '\')
    if (-not (Test-Path -LiteralPath $file)) { Say "Missing file: $($parts[1])"; exit 1 }
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLower() -ne $parts[0]) {
        Say "Checksum mismatch: $($parts[1]); re-download the kit."; exit 1
    }
}
Say 'Kit integrity OK.'

# (2) Locate uv
$uv = $null
$cmd = Get-Command uv -ErrorAction SilentlyContinue
if ($cmd) { $uv = $cmd.Source }
elseif (Test-Path -LiteralPath (Join-Path $UvRoot 'uv.exe')) { $uv = Join-Path $UvRoot 'uv.exe' }

# (3) Download list and consent
Say 'This setup will download:'
if (-not $uv) { Say "  - uv $UvVersion zip from github.com/astral-sh/uv ($UvUrl)" }
Say '  - CPython 3.12 (only if no Python >= 3.11) from github.com/astral-sh/python-build-standalone'
Say '  - pydantic and psutil from pypi.org, at the versions in constraints.txt'
if ($DryRun) {
    Plan 'ask "Continue? [y/N]"'
} else {
    $ans = Read-Host 'Continue? [y/N]'
    if ($ans -ne 'y') { Say 'Nothing changed.'; exit 0 }
}

# (4) Install uv if missing
if (-not $uv) {
    $zip = Join-Path $env:TEMP 'uv-x86_64-pc-windows-msvc.zip'
    if ($DryRun) {
        Plan "download $UvUrl to $zip, verify SHA-256, expand to $UvRoot"
        $uv = Join-Path $UvRoot 'uv.exe'
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $UvUrl -OutFile $zip -UseBasicParsing
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLower() -ne $UvSha256) {
            Remove-Item -LiteralPath $zip -Force
            Say 'uv download checksum mismatch; deleted.'; exit 1
        }
        Expand-Archive -LiteralPath $zip -DestinationPath $UvRoot -Force
        # UNVERIFIED: uv release zip holds uv.exe at its root
        $uv = Join-Path $UvRoot 'uv.exe'
        if (-not (Test-Path -LiteralPath $uv)) { Say "uv.exe not found at $UvRoot"; exit 1 }
    }
}

# (5) Install sae
$wheel = (Get-ChildItem -LiteralPath $Kit -Filter '*.whl' | Select-Object -First 1)
if (-not $wheel) { Say 'No wheel in kit.'; exit 1 }
$constraints = Join-Path $Kit 'constraints.txt'
if ($DryRun) {
    Plan "$uv tool install --python 3.12 --reinstall -c $constraints $($wheel.FullName)"
} else {
    & $uv tool install --python 3.12 --reinstall -c $constraints $wheel.FullName
    if ($LASTEXITCODE -ne 0) { Say 'uv tool install failed.'; exit 1 }
}

# (6) Parser exe and PATH
if ($DryRun) {
    Plan 'copy bin\sae-parse.exe into the folder printed by: uv tool dir --bin'
    Plan 'ask to run: uv tool update-shell (only if that folder is not on the user PATH)'
    Plan 'prepend that folder to PATH for this session'
} else {
    $bin = (& $uv tool dir --bin | Select-Object -First 1).Trim()
    Copy-Item -LiteralPath (Join-Path $Kit 'bin\sae-parse.exe') -Destination $bin -Force
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($null -eq $userPath) { $userPath = '' }
    if (-not (($userPath -split ';') -contains $bin)) {
        $a = Read-Host "Add $bin to your user PATH? [y/N]"
        if ($a -eq 'y') { & $uv tool update-shell }
    }
    $env:PATH = "$bin;$env:PATH"
}

# (7) Doctor
$results = Join-Path $Kit 'm0_results'
if ($DryRun) {
    Plan "sae m0 --kit-dir $Kit --results $results doctor"
} else {
    & sae m0 --kit-dir "$Kit" --results "$results" doctor
    if ($LASTEXITCODE -ne 0) { Write-Warning 'doctor reported problems; continuing.' }
}

# (8) Next steps (printed only)
Say ''
Say 'Next steps (do these by hand):'
Say "  1. Copy the folder $Kit\mod\sae_bridge to <Stellaris user folder>\mod\sae_bridge (see the doctor line 'copy by hand')."
Say '  2. Run: sae m0 stage M0-2   (writes the .mod file for you to copy)'
Say "  3. Then: cd $Kit; sae m0 steps M0-2"
Say 'Uninstall: uv tool uninstall sae; delete sae-parse.exe from the uv tool bin folder; delete the kit folder.'
exit 0
