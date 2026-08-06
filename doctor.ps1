param(
    [string]$RuntimeDirectory = ""
)

$ErrorActionPreference = "Stop"

$appRoot = $PSScriptRoot
$runtimeCandidates = @()
if (-not [string]::IsNullOrWhiteSpace($RuntimeDirectory)) {
    $runtimeCandidates += [System.IO.Path]::GetFullPath($RuntimeDirectory)
}
if (-not [string]::IsNullOrWhiteSpace($env:PDF_PAPER_TRANSLATOR_RUNTIME)) {
    $runtimeCandidates += [System.IO.Path]::GetFullPath(
        $env:PDF_PAPER_TRANSLATOR_RUNTIME
    )
}
$runtimeCandidates += (Join-Path $appRoot ".venv")
$runtimeCandidates += (Join-Path (Split-Path -Parent $appRoot) ".venv-pdf2zh-next")

$activeRuntime = $null
foreach ($candidate in $runtimeCandidates) {
    if (Test-Path -LiteralPath (Join-Path $candidate "Scripts\pdf2zh.exe")) {
        $activeRuntime = $candidate
        break
    }
}

$checks = @(
    [pscustomobject]@{
        Name = "Windows PowerShell"
        Ready = ($PSVersionTable.PSVersion.Major -ge 5)
        Detail = $PSVersionTable.PSVersion.ToString()
    },
    [pscustomobject]@{
        Name = "Application runtime"
        Ready = (-not [string]::IsNullOrWhiteSpace($activeRuntime))
        Detail = if ($null -eq $activeRuntime) {
            (Join-Path $appRoot ".venv")
        }
        else {
            $activeRuntime
        }
    },
    [pscustomobject]@{
        Name = "DEEPSEEK_API_KEY"
        Ready = (
            -not [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY) -or
            -not [string]::IsNullOrWhiteSpace(
                [Environment]::GetEnvironmentVariable(
                    "DEEPSEEK_API_KEY",
                    "User"
                )
            )
        )
        Detail = "Checked without displaying the secret"
    }
)

$checks | Format-Table Name, Ready, Detail -AutoSize

if ($checks.Ready -contains $false) {
    Write-Error "One or more required checks failed."
    exit 1
}

Write-Output "All required checks passed."
