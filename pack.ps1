$ErrorActionPreference = "Stop"

$appRoot = $PSScriptRoot
$version = (Get-Content -Raw -LiteralPath (Join-Path $appRoot "VERSION")).Trim()
$distDirectory = Join-Path $appRoot "dist"
$archivePath = Join-Path $distDirectory "pdf-paper-translator-$version.zip"
$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "pdf-paper-translator-package-" + [guid]::NewGuid().ToString("N")
)
$stagingApp = Join-Path $stagingRoot "pdf-paper-translator"

try {
    New-Item -ItemType Directory -Path $stagingApp -Force | Out-Null
    $packageFiles = @(
        "pdftranslate.ps1",
        "pdftranslate.cmd",
        "paperflow.py",
        "paperflow.ps1",
        "paperflow.cmd",
        "install.ps1",
        "install.cmd",
        "doctor.ps1",
        "self-test.ps1",
        "requirements.txt",
        "README.md",
        "NOTICE.md",
        "VERSION",
        ".gitignore"
    )

    foreach ($file in $packageFiles) {
        Copy-Item -LiteralPath (Join-Path $appRoot $file) `
            -Destination (Join-Path $stagingApp $file)
    }

    if (-not (Test-Path -LiteralPath $distDirectory)) {
        New-Item -ItemType Directory -Path $distDirectory | Out-Null
    }
    Compress-Archive -LiteralPath $stagingApp -DestinationPath $archivePath -Force
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}

Write-Output $archivePath
