param(
    [string]$RuntimeDirectory = ""
)

$ErrorActionPreference = "Stop"
$appRoot = $PSScriptRoot
$projectRoot = Split-Path -Parent $appRoot
$translateScript = Join-Path $appRoot "pdftranslate.ps1"
$doctorScript = Join-Path $appRoot "doctor.ps1"
$failures = New-Object System.Collections.Generic.List[string]
$selfTestTemp = $null

function Resolve-PaperflowPython {
    $pythonCandidates = @()
    if (-not [string]::IsNullOrWhiteSpace($RuntimeDirectory)) {
        $pythonCandidates += Join-Path $RuntimeDirectory "Scripts\python.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:PDF_PAPER_TRANSLATOR_RUNTIME)) {
        $pythonCandidates += Join-Path `
            $env:PDF_PAPER_TRANSLATOR_RUNTIME "Scripts\python.exe"
    }
    $pythonCandidates += Join-Path $appRoot ".venv\Scripts\python.exe"
    $pythonCandidates += Join-Path `
        (Split-Path -Parent $appRoot) ".venv-pdf2zh-next\Scripts\python.exe"
    return $pythonCandidates | Where-Object {
        Test-Path -LiteralPath $_
    } | Select-Object -First 1
}

function Test-Step {
    param(
        [string]$Name,
        [scriptblock]$Action
    )
    try {
        & $Action
        Write-Output "[PASS] $Name"
    }
    catch {
        $script:failures.Add("$Name - $($_.Exception.Message)")
        Write-Output "[FAIL] $Name"
    }
}

$runtimeArgs = @{}
if (-not [string]::IsNullOrWhiteSpace($RuntimeDirectory)) {
    $runtimeArgs.RuntimeDirectory = $RuntimeDirectory
}

Test-Step "PowerShell syntax" {
    foreach ($scriptName in @(
        "pdftranslate.ps1",
        "paperflow.ps1",
        "doctor.ps1",
        "install.ps1",
        "pack.ps1",
        "self-test.ps1"
    )) {
        $scriptPath = Join-Path $appRoot $scriptName
        if (-not (Test-Path -LiteralPath $scriptPath)) {
            continue
        }
        $tokens = $null
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile(
            $scriptPath,
            [ref]$tokens,
            [ref]$parseErrors
        )
        if ($parseErrors.Count -gt 0) {
            throw "$scriptName has syntax errors."
        }
    }
}

Test-Step "Paperflow Python syntax and CLI" {
    $python = Resolve-PaperflowPython
    if ($null -eq $python) {
        throw "Python runtime was not found."
    }
    & $python -X utf8 -c `
        "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" `
        (Join-Path $appRoot "paperflow.py")
    if ($LASTEXITCODE -ne 0) {
        throw "paperflow.py failed compilation."
    }
    & $python -X utf8 (Join-Path $appRoot "paperflow.py") --help | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "paperflow CLI help failed."
    }
    & $python -X utf8 (Join-Path $appRoot "paperflow.py") self-check | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "paperflow internal self-check failed."
    }
}

Test-Step "Runtime and API-key doctor" {
    & $doctorScript @runtimeArgs | Out-Null
}

$rootPdf = Get-ChildItem -LiteralPath $projectRoot -Filter "*.pdf" -File |
    Select-Object -First 1
if ($null -eq $rootPdf -and (Test-Path -LiteralPath (Join-Path $projectRoot "papers"))) {
    $rootPdf = Get-ChildItem -LiteralPath (Join-Path $projectRoot "papers") `
        -Filter "paper.pdf" -File -Recurse | Select-Object -First 1
}
if ($null -eq $rootPdf) {
    $python = Resolve-PaperflowPython
    if ($null -eq $python) {
        throw "No PDF or Python runtime is available for preflight tests."
    }
    $selfTestTemp = Join-Path ([System.IO.Path]::GetTempPath()) (
        "pdf-paper-translator-self-test-" + [guid]::NewGuid().ToString("N")
    )
    New-Item -ItemType Directory -Path $selfTestTemp | Out-Null
    $fixturePath = Join-Path $selfTestTemp "fixture.pdf"
    & $python -X utf8 -c `
        "import fitz,sys; d=fitz.open(); d.new_page(); d.save(sys.argv[1])" `
        $fixturePath
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the temporary PDF fixture."
    }
    $rootPdf = Get-Item -LiteralPath $fixturePath
}

Test-Step "New-paper full-translation preflight" {
    $testPaperId = "self-test-" + [guid]::NewGuid().ToString("N")
    $output = & $translateScript $rootPdf.FullName `
        -PaperId $testPaperId @runtimeArgs -Preflight
    if (($output -join "`n") -notmatch "Preflight: OK") {
        throw "Preflight did not report success."
    }
    if (Test-Path -LiteralPath (Join-Path $projectRoot "papers\$testPaperId")) {
        throw "Preflight unexpectedly wrote an archive directory."
    }
}

$archivedPdf = $null
if (Test-Path -LiteralPath (Join-Path $projectRoot "papers")) {
    $archivedPdf = Get-ChildItem -LiteralPath (Join-Path $projectRoot "papers") `
        -Filter "paper.pdf" -File -Recurse | Select-Object -First 1
}
if ($null -ne $archivedPdf) {
    Test-Step "Archived-paper preflight" {
        & $translateScript $archivedPdf.FullName @runtimeArgs -Preflight |
            Out-Null
    }
}

Test-Step "Chinese and spaced library-path preflight" {
    $customLibrary = Join-Path $projectRoot (
        "tmp\自测 路径 " + [guid]::NewGuid().ToString("N") + "\papers"
    )
    & $translateScript $rootPdf.FullName -PaperId "path-test" `
        -LibraryRoot $customLibrary @runtimeArgs -Preflight | Out-Null
    if (Test-Path -LiteralPath $customLibrary) {
        throw "Preflight unexpectedly created the custom library."
    }
}

Test-Step "Invalid input fails safely" {
    $didFail = $false
    try {
        & $translateScript (Join-Path $appRoot "README.md") `
            @runtimeArgs -Preflight | Out-Null
    }
    catch {
        $didFail = $true
    }
    if (-not $didFail) {
        throw "A non-PDF input was accepted."
    }
}

$papersPath = Join-Path $projectRoot "papers"
if (Test-Path -LiteralPath $papersPath) {
Test-Step "Existing translation artifacts (development repository)" {
    $paperDirectories = Get-ChildItem -LiteralPath (Join-Path $projectRoot "papers") `
        -Directory
    $complete = $paperDirectories | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName "paper_zh-CN.dual.pdf")) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName "paper_zh-CN.mono.pdf")) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName "translation-report.md"))
    } | Where-Object {
        $candidateReport = Get-Content -Raw -LiteralPath (
            Join-Path $_.FullName "translation-report.md"
        )
        $candidateReport -match '(?im)tokens[^\r\n]*[\d,]+' -and
        $candidateReport -match '\$\d+\.\d+'
    } | Select-Object -First 1
    if ($null -eq $complete) {
        throw "No completed translation archive was found."
    }
    foreach ($name in @("paper_zh-CN.dual.pdf", "paper_zh-CN.mono.pdf")) {
        $path = Join-Path $complete.FullName $name
        $bytes = [System.IO.File]::ReadAllBytes($path)
        if (
            $bytes.Length -lt 4 -or
            [System.Text.Encoding]::ASCII.GetString($bytes, 0, 4) -ne "%PDF"
        ) {
            throw "$name is not a recognizable PDF."
        }
    }
    $report = Get-Content -Raw -LiteralPath (
        Join-Path $complete.FullName "translation-report.md"
    )
    if ($report -notmatch '(?im)tokens[^\r\n]*[\d,]+' -or
        $report -notmatch '\$\d+\.\d+' ) {
        throw "The translation report lacks parsed token or cost data."
    }
}
}

$version = (Get-Content -Raw -LiteralPath (Join-Path $appRoot "VERSION")).Trim()
$zipPath = Join-Path $appRoot "dist\pdf-paper-translator-$version.zip"
if (Test-Path -LiteralPath $zipPath) {
    Test-Step "Distribution archive safety" {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
        try {
            $names = $archive.Entries.FullName
            $nameText = $names -join "`n"
            if ($nameText -match '(^|[\\/])(\.venv|papers|\.env)([\\/]|$)') {
                throw "The package contains runtime, papers, or an environment file."
            }
            if ($nameText -notmatch "self-test\.ps1") {
                throw "The package does not contain self-test.ps1."
            }
            if ($nameText -notmatch "paperflow\.py") {
                throw "The package does not contain paperflow.py."
            }
        }
        finally {
            $archive.Dispose()
        }
    }
}

Write-Output ""
if ($failures.Count -gt 0) {
    if ($null -ne $selfTestTemp -and (Test-Path -LiteralPath $selfTestTemp)) {
        Remove-Item -LiteralPath $selfTestTemp -Recurse -Force
    }
    Write-Output "Self-test failed:"
    $failures | ForEach-Object { Write-Output "  $_" }
    exit 1
}

if ($null -ne $selfTestTemp -and (Test-Path -LiteralPath $selfTestTemp)) {
    Remove-Item -LiteralPath $selfTestTemp -Recurse -Force
}
Write-Output "All translator self-tests passed. No translation API call was made."
