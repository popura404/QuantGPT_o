param(
    [string]$Python = "",
    [string]$Venv = ".venv312"
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    if ($Python) {
        $Version = & $Python -c 'import sys; print("%d.%d" % sys.version_info[:2])'
    } else {
        $Version = & py -3.12 -c 'import sys; print("%d.%d" % sys.version_info[:2])'
    }
    if ($LASTEXITCODE -ne 0 -or $Version -ne "3.12") {
        throw "Install CPython 3.12 or pass -Python with its executable path."
    }
    $VenvPython = Join-Path $Venv "Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        if ($Python) { & $Python -m venv $Venv } else { & py -3.12 -m venv $Venv }
        if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
    }
    $VenvVersion = & $VenvPython -c 'import sys; print("%d.%d" % sys.version_info[:2])'
    if ($LASTEXITCODE -ne 0 -or $VenvVersion -ne "3.12") {
        throw "The selected environment is not Python 3.12; use a new -Venv path."
    }
    & $VenvPython -m pip install --require-hashes -r requirements/windows-py312.lock
    if ($LASTEXITCODE -ne 0) { throw "Locked dependency installation failed." }
    & $VenvPython -m pip install --no-deps --no-build-isolation -e .
    if ($LASTEXITCODE -ne 0) { throw "Editable project installation failed." }
    if (-not (Test-Path -LiteralPath ".env")) {
        Copy-Item -LiteralPath ".env.example" -Destination ".env"
    }
    Write-Host "Installed CPython 3.12 environment at $Venv. Start with ./scripts/dev.ps1 -Venv $Venv"
} finally {
    Pop-Location
}
