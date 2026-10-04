param(
    [string]$Venv = ".venv312",
    [int]$Port = 8003,
    [string]$BindAddress = "127.0.0.1",
    [ValidateSet("http", "stdio", "sse", "streamable-http")]
    [string]$Transport = "http"
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $VenvPython = Join-Path $Venv "Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) { throw "Run ./scripts/setup.ps1 first." }
    & $VenvPython -m quantgpt --transport $Transport --host $BindAddress --port $Port
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
