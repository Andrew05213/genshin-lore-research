# Apache-2.0. Bootstrap only; data qualification stays in the existing builders.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = '1'
$logs = Join-Path (Get-Location) 'data\setup-logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
Start-Transcript -Path (Join-Path $logs ('bootstrap-' + [guid]::NewGuid().ToString('N') + '.log')) | Out-Null

function Require-Tool($Command, $Package) {
    if (Get-Command $Command -ErrorAction SilentlyContinue) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Missing $Command. Install $Package, then run setup.cmd again."
    }
    & winget install --id $Package --exact --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Installation failed: $Package ($LASTEXITCODE)" }
    # New processes inherit updated installer paths, without changing user settings.
    $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
}

function Find-Python312 {
    # A missing 3.12 installation is an expected probe failure, not a fatal error.
    $ErrorActionPreference = 'Continue'
    foreach ($launcher in @('py', 'python')) {
        if (-not (Get-Command $launcher -ErrorAction SilentlyContinue)) { continue }
        $probe = @('-c', 'import sys; print(sys.executable); sys.exit(0 if sys.version_info[:2] == (3,12) else 1)')
        if ($launcher -eq 'py') { $probe = @('-3.12') + $probe }
        $found = & $launcher @probe 2>$null
        if ($LASTEXITCODE -eq 0) { return ($found | Select-Object -Last 1) }
    }
    return $null
}

try {
    Require-Tool 'git' 'Git.Git'
    $python = Find-Python312
    if (-not $python) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'Install Python 3.12, then run setup.cmd again.' }
        & winget install --id Python.Python.3.12 --exact --source winget --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
        $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
        $python = Find-Python312
        if (-not $python) { throw 'Python installed. Reopen setup.cmd to refresh the launcher.' }
    }
    # hnswlib has no guaranteed Windows wheel; prepare the supported native compiler.
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $compiler = $null
    if (Test-Path $vswhere) {
        $compiler = & $vswhere -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    }
    if (-not $compiler) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'Install Visual Studio C++ Build Tools (VCTools and Windows SDK), then retry.' }
        Write-Host 'Installing C++ Build Tools for hnswlib; this can download several GB.'
        & winget install --id Microsoft.VisualStudio.2022.BuildTools --exact --source winget --accept-source-agreements --accept-package-agreements --override '--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended'
        if ($LASTEXITCODE -ne 0) { throw 'C++ Build Tools installation failed or requires restart. Retry setup.cmd after resolving the installer message.' }
        $compiler = & $vswhere -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if (-not $compiler) { throw 'C++ toolchain is not ready. Complete installation/restart and retry setup.cmd.' }
    }
    & $python (Join-Path $PSScriptRoot 'setup_windows.py') @args
    exit $LASTEXITCODE
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
