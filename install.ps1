param(
    [string]$PythonVersion = "3.12",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$appRoot = $PSScriptRoot
$runtimeDirectory = Join-Path $appRoot ".venv"
$pdf2zh = Join-Path $runtimeDirectory "Scripts\pdf2zh.exe"
$requirements = Join-Path $appRoot "requirements.txt"

if ((Test-Path -LiteralPath $pdf2zh) -and -not $Force) {
    Write-Output "Runtime is already installed: $runtimeDirectory"
    Write-Output "Use -Force only when you intentionally want to reinstall it."
    exit 0
}

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    throw "uv is required. Install it from https://docs.astral.sh/uv/ and rerun this installer."
}

if ($Force -and (Test-Path -LiteralPath $runtimeDirectory)) {
    $resolvedAppRoot = [System.IO.Path]::GetFullPath($appRoot).TrimEnd("\")
    $resolvedRuntime = [System.IO.Path]::GetFullPath($runtimeDirectory)
    if (-not $resolvedRuntime.StartsWith(
        $resolvedAppRoot + "\",
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to remove a runtime outside the application folder."
    }
    Remove-Item -LiteralPath $resolvedRuntime -Recurse -Force
}

& $uv.Source venv $runtimeDirectory --python $PythonVersion
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the application runtime."
}

& $uv.Source pip install `
    --python (Join-Path $runtimeDirectory "Scripts\python.exe") `
    --requirement $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install application dependencies."
}

if (-not (Test-Path -LiteralPath $pdf2zh)) {
    throw "Installation completed without creating pdf2zh.exe."
}

Write-Output ""
Write-Output "Installation completed."
Write-Output "Run doctor.ps1 to verify the runtime and API key."
