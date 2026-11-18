param(
    [string]$Venv = ".venv312",
    [int]$Port = 8003,
    [string]$BindAddress = "127.0.0.1",
    [ValidateSet("http", "stdio", "sse", "streamable-http")]
    [string]$Transport = "http",
    [switch]$BuildFrontend
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    $VenvPython = Join-Path $Venv "Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython)) { throw "Run ./scripts/setup.ps1 first." }
    if ($BuildFrontend) {
        # Keep build output off stdout when the next process serves MCP over stdio.
        if (-not (Test-Path -LiteralPath "frontend/node_modules" -PathType Container)) {
            & npm.cmd --prefix frontend ci | ForEach-Object { [Console]::Error.WriteLine($_) }
            if ($LASTEXITCODE -ne 0) { throw "Frontend dependency installation failed (exit $LASTEXITCODE)." }
        }
        & npm.cmd --prefix frontend run build | ForEach-Object { [Console]::Error.WriteLine($_) }
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed (exit $LASTEXITCODE)." }
    }
    & $VenvPython -m quantgpt --transport $Transport --host $BindAddress --port $Port
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
