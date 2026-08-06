param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PaperflowArguments
)

$ErrorActionPreference = "Stop"
$appRoot = $PSScriptRoot
$runtimeCandidates = @()
if (-not [string]::IsNullOrWhiteSpace($env:PDF_PAPER_TRANSLATOR_RUNTIME)) {
    $runtimeCandidates += [System.IO.Path]::GetFullPath(
        $env:PDF_PAPER_TRANSLATOR_RUNTIME
    )
}
$runtimeCandidates += (Join-Path $appRoot ".venv")
$runtimeCandidates += (Join-Path (Split-Path -Parent $appRoot) ".venv-pdf2zh-next")

$python = $null
foreach ($candidate in $runtimeCandidates) {
    $executable = Join-Path $candidate "Scripts\python.exe"
    if (Test-Path -LiteralPath $executable) {
        $python = $executable
        break
    }
}
if ([string]::IsNullOrWhiteSpace($python)) {
    throw "Application runtime not found. Run install.ps1 first."
}

& $python -X utf8 (Join-Path $appRoot "paperflow.py") @PaperflowArguments
exit $LASTEXITCODE
