param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Pdf,

    [string]$PaperId = "",
    [string]$LibraryRoot = "",
    [string]$RuntimeDirectory = "",
    [string]$Pages = "",
    [string]$OutputDirectory = "",
    [string]$Model = "deepseek-v4-flash",
    [ValidateRange(1, 20)]
    [int]$Qps = 2,
    [ValidateRange(1, 20)]
    [int]$Workers = 2,
    [switch]$TranslateTableText,
    [switch]$EnableAutoGlossary,
    [switch]$SkipScannedDetection,
    [switch]$Preflight
)

$ErrorActionPreference = "Stop"

function Get-TokenMetric {
    param(
        [object[]]$Lines,
        [string]$Label
    )

    $pattern = [regex]::Escape($Label) + ":\s*([\d,]+)"
    for ($index = $Lines.Count - 1; $index -ge 0; $index--) {
        $text = $Lines[$index].ToString()
        if ($text -match $pattern) {
            return [int64](($Matches[1]) -replace ",", "")
        }
    }
    return $null
}

function Convert-ToYamlQuotedString {
    param([string]$Value)
    return '"' + ($Value -replace '\\', '\\' -replace '"', '\"') + '"'
}

$appRoot = $PSScriptRoot
$sourcePdfPath = (Resolve-Path -LiteralPath $Pdf).Path
$originalFileName = Split-Path -Leaf $sourcePdfPath

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

$pdf2zh = $null
foreach ($candidate in $runtimeCandidates) {
    $candidateExecutable = Join-Path $candidate "Scripts\pdf2zh.exe"
    if (Test-Path -LiteralPath $candidateExecutable) {
        $pdf2zh = $candidateExecutable
        break
    }
}

if ([string]::IsNullOrWhiteSpace($pdf2zh)) {
    throw "PDFMathTranslate runtime not found. Run install.ps1 in the application folder."
}

if ([System.IO.Path]::GetExtension($sourcePdfPath) -ne ".pdf") {
    throw "Input is not a PDF: $sourcePdfPath"
}

$apiKey = $env:DEEPSEEK_API_KEY
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    $apiKey = [Environment]::GetEnvironmentVariable("DEEPSEEK_API_KEY", "User")
}
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw "DEEPSEEK_API_KEY is not set in the current process or user environment."
}

$sourceParent = Split-Path -Parent $sourcePdfPath
$looksLikeArchivedPaper = (
    ((Split-Path -Leaf $sourcePdfPath) -eq "paper.pdf") -and
    (Test-Path -LiteralPath (Join-Path $sourceParent "metadata.yaml"))
)

if ($looksLikeArchivedPaper) {
    $papersRoot = Split-Path -Parent $sourceParent
}
elseif (-not [string]::IsNullOrWhiteSpace($LibraryRoot)) {
    $papersRoot = [System.IO.Path]::GetFullPath($LibraryRoot)
}
else {
    $papersRoot = Join-Path $sourceParent "papers"
}

$papersPrefix = $papersRoot.TrimEnd("\") + "\"
$isArchivedPaper = (
    $looksLikeArchivedPaper -or (
        $sourceParent.StartsWith(
            $papersPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and
        ((Split-Path -Leaf $sourcePdfPath) -eq "paper.pdf")
    )
)

if ($isArchivedPaper) {
    $paperDirectory = $sourceParent
    $pdfPath = $sourcePdfPath
}
else {
    if ([string]::IsNullOrWhiteSpace($PaperId)) {
        $PaperId = [System.IO.Path]::GetFileNameWithoutExtension($sourcePdfPath)
    }
    if (
        $PaperId -in @(".", "..") -or
        $PaperId.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
        $PaperId.Contains("\") -or
        $PaperId.Contains("/")
    ) {
        throw "PaperId must be a single valid folder name: $PaperId"
    }

    $paperDirectory = Join-Path $papersRoot $PaperId
    $pdfPath = Join-Path $paperDirectory "paper.pdf"
    if (Test-Path -LiteralPath $pdfPath) {
        $sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePdfPath).Hash
        $archiveHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $pdfPath).Hash
        if ($sourceHash -ne $archiveHash) {
            throw "The paper archive already contains a different paper.pdf: $paperDirectory"
        }
    }
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    if ([string]::IsNullOrWhiteSpace($Pages)) {
        $outputPath = $paperDirectory
    }
    else {
        $pageLabel = ($Pages -replace "[^0-9A-Za-z]+", "-").Trim("-")
        if ([string]::IsNullOrWhiteSpace($pageLabel)) {
            $pageLabel = "selected"
        }
        $outputPath = Join-Path $paperDirectory "trials\pages-$pageLabel"
    }
}
elseif ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
}
else {
    $outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
}

if ($Preflight) {
    $scope = if ([string]::IsNullOrWhiteSpace($Pages)) {
        "full document"
    }
    else {
        "diagnostic pages: $Pages"
    }
    Write-Output "Preflight: OK"
    Write-Output "Source PDF: $sourcePdfPath"
    Write-Output "Runtime: $pdf2zh"
    Write-Output "API key: configured (value hidden)"
    Write-Output "Paper archive: $paperDirectory"
    Write-Output "Output: $outputPath"
    Write-Output "Scope: $scope"
    return
}

if (-not (Test-Path -LiteralPath $papersRoot)) {
    New-Item -ItemType Directory -Path $papersRoot -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $paperDirectory)) {
    New-Item -ItemType Directory -Path $paperDirectory -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $pdfPath)) {
    Copy-Item -LiteralPath $sourcePdfPath -Destination $pdfPath
}

$metadataPath = Join-Path $paperDirectory "metadata.yaml"
if (-not (Test-Path -LiteralPath $metadataPath)) {
    $metadata = @"
title: ""
authors: []
year:
venue: ""
source_url: ""
original_file: $(Convert-ToYamlQuotedString $originalFileName)
archived_file: "paper.pdf"
translation_status: "not_started"
reading_status: "unread"
"@
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($metadataPath, $metadata, $utf8NoBom)
}

if (-not (Test-Path -LiteralPath $outputPath)) {
    New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
}

$previousPdf2zhKey = $env:PDF2ZH_DEEPSEEK_API_KEY
$env:PDF2ZH_DEEPSEEK_API_KEY = $apiKey

$arguments = @(
    "--deepseek",
    "--deepseek-model", $Model,
    "--deepseek-thinking-mode", "disabled",
    "--lang-in", "en",
    "--lang-out", "zh",
    "--output", $outputPath,
    "--qps", $Qps,
    "--pool-max-workers", $Workers,
    "--watermark-output-mode", "no_watermark"
)

if (-not $EnableAutoGlossary) {
    $arguments += "--no-auto-extract-glossary"
}
if ($TranslateTableText) {
    $arguments += "--translate-table-text"
}
if ($SkipScannedDetection) {
    $arguments += "--skip-scanned-detection"
}
if (-not [string]::IsNullOrWhiteSpace($Pages)) {
    $arguments += @("--pages", $Pages, "--only-include-translated-page")
}
$arguments += $pdfPath

$startedAt = Get-Date
$capturedOutput = @()
$exitCode = -1
$previousErrorActionPreference = $ErrorActionPreference

try {
    # Native applications may write normal progress information to stderr.
    # Windows PowerShell 5.1 wraps such lines as non-terminating ErrorRecord
    # objects, so temporarily continue and judge success by LASTEXITCODE.
    $ErrorActionPreference = "Continue"
    & $pdf2zh @arguments 2>&1 | Tee-Object -Variable capturedOutput
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "PDFMathTranslate exited with code $exitCode."
    }
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
    if ($null -eq $previousPdf2zhKey) {
        Remove-Item Env:PDF2ZH_DEEPSEEK_API_KEY -ErrorAction SilentlyContinue
    }
    else {
        $env:PDF2ZH_DEEPSEEK_API_KEY = $previousPdf2zhKey
    }
}

$finishedAt = Get-Date
$durationSeconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 2)

$rawMono = Join-Path $outputPath "paper.no_watermark.zh.mono.pdf"
$rawDual = Join-Path $outputPath "paper.no_watermark.zh.dual.pdf"
$monoOutput = Join-Path $outputPath "paper_zh-CN.mono.pdf"
$dualOutput = Join-Path $outputPath "paper_zh-CN.dual.pdf"

if (Test-Path -LiteralPath $rawMono) {
    Move-Item -LiteralPath $rawMono -Destination $monoOutput -Force
}
if (Test-Path -LiteralPath $rawDual) {
    Move-Item -LiteralPath $rawDual -Destination $dualOutput -Force
}

$totalTokens = Get-TokenMetric -Lines $capturedOutput -Label "Total tokens"
$promptTokens = Get-TokenMetric -Lines $capturedOutput -Label "Prompt tokens"
$completionTokens = Get-TokenMetric -Lines $capturedOutput -Label "Completion tokens"
$cacheHitTokens = Get-TokenMetric -Lines $capturedOutput -Label "Cache hit prompt tokens"

$capturedText = ($capturedOutput | ForEach-Object {
    $_.ToString()
}) -join "`n"
$currentUsagePattern = (
    "Total Token Usage:[\s\S]{0,500}?" +
    "Total\s+([\d,]+),\s*" +
    "Prompt\s+([\d,]+),\s*" +
    "Cache Hit Prompt\s+([\d,]+),\s*" +
    "Completion\s+([\d,]+)"
)
if (
    ($null -eq $totalTokens -or $null -eq $promptTokens -or
        $null -eq $completionTokens -or $null -eq $cacheHitTokens) -and
    $capturedText -match $currentUsagePattern
) {
    $totalTokens = [int64](($Matches[1]) -replace ",", "")
    $promptTokens = [int64](($Matches[2]) -replace ",", "")
    $cacheHitTokens = [int64](($Matches[3]) -replace ",", "")
    $completionTokens = [int64](($Matches[4]) -replace ",", "")
}

$cacheMissTokens = $null
if ($null -ne $promptTokens -and $null -ne $cacheHitTokens) {
    $cacheMissTokens = [Math]::Max(0, $promptTokens - $cacheHitTokens)
}

$priceTable = @{
    "deepseek-v4-flash" = @{
        CacheHit = 0.0028
        CacheMiss = 0.14
        Output = 0.28
    }
    "deepseek-v4-pro" = @{
        CacheHit = 0.003625
        CacheMiss = 0.435
        Output = 0.87
    }
}

$estimatedCost = $null
if (
    $priceTable.ContainsKey($Model) -and
    $null -ne $cacheHitTokens -and
    $null -ne $cacheMissTokens -and
    $null -ne $completionTokens
) {
    $prices = $priceTable[$Model]
    $estimatedCost = (
        $cacheHitTokens * $prices.CacheHit +
        $cacheMissTokens * $prices.CacheMiss +
        $completionTokens * $prices.Output
    ) / 1000000
}

function Format-Metric {
    param($Value)
    if ($null -eq $Value) {
        return "未记录"
    }
    return ([int64]$Value).ToString("N0")
}

$costText = "未计算（模型无价格快照或运行日志未返回完整 token）"
if ($null -ne $estimatedCost) {
    $costText = '$' + $estimatedCost.ToString(
        "0.000000",
        [System.Globalization.CultureInfo]::InvariantCulture
    )
}

$translationScope = "全文"
if (-not [string]::IsNullOrWhiteSpace($Pages)) {
    $translationScope = "选页：$Pages"
}

$monoStatus = if (Test-Path -LiteralPath $monoOutput) {
    Split-Path -Leaf $monoOutput
}
else {
    "未发现"
}
$dualStatus = if (Test-Path -LiteralPath $dualOutput) {
    Split-Path -Leaf $dualOutput
}
else {
    "未发现"
}

$reportPath = Join-Path $outputPath "translation-report.md"
$reportEntry = @"

## $($finishedAt.ToString("yyyy-MM-dd HH:mm:ss zzz"))

### 基本信息

- 原始文件名：``$originalFileName``
- 归档原文：``paper.pdf``
- 翻译范围：$translationScope
- 翻译服务：DeepSeek
- 翻译模型：``$Model``
- 思考模式：``disabled``
- 翻译工具：PDFMathTranslate ``2.9.0``
- 排版后端：BabelDOC ``0.6.2``
- QPS / Workers：``$Qps / $Workers``
- 运行耗时：$durationSeconds 秒
- 退出状态：成功（``$exitCode``）

### Token 与费用

- 总 tokens：$(Format-Metric $totalTokens)
- 输入 tokens：$(Format-Metric $promptTokens)
- 缓存命中输入 tokens：$(Format-Metric $cacheHitTokens)
- 缓存未命中输入 tokens：$(Format-Metric $cacheMissTokens)
- 输出 tokens：$(Format-Metric $completionTokens)
- 估算费用（美元）：$costText
- 账户实际扣费：待在 DeepSeek 控制台核对

费用按照脚本内置的每百万 token 价格快照估算。DeepSeek 调价后，应以控制台账单为准。

### 输出文件

- 中文译文：``$monoStatus``
- 中英对照：``$dualStatus``

### 质量检查

- [ ] 页数完整
- [ ] 双栏阅读顺序正确
- [ ] 公式、图表和表格未损坏
- [ ] 数字、百分比和比较方向与原文一致
- [ ] 模型名、数据集名和专业术语翻译一致

### 已知问题

- 待人工校对。
"@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
if (Test-Path -LiteralPath $reportPath) {
    [System.IO.File]::AppendAllText($reportPath, $reportEntry, $utf8NoBom)
}
else {
    $reportHeader = @"
# 翻译报告

> 本文件由翻译脚本自动维护。费用为估算值，译文为机器翻译，需人工校对。
"@
    [System.IO.File]::WriteAllText(
        $reportPath,
        $reportHeader + $reportEntry,
        $utf8NoBom
    )
}

$metadataContent = [System.IO.File]::ReadAllText(
    $metadataPath,
    [System.Text.Encoding]::UTF8
)
$translationStatus = if ([string]::IsNullOrWhiteSpace($Pages)) {
    "completed"
}
else {
    "pilot_completed"
}
if ($metadataContent -match "(?m)^translation_status:") {
    $metadataContent = $metadataContent -replace `
        "(?m)^translation_status:.*$", `
        "translation_status: `"$translationStatus`""
}
else {
    $metadataContent = $metadataContent.TrimEnd() +
        "`r`ntranslation_status: `"$translationStatus`"`r`n"
}
[System.IO.File]::WriteAllText($metadataPath, $metadataContent, $utf8NoBom)

Write-Output ""
Write-Output "Paper archive: $paperDirectory"
Write-Output "Translation report: $reportPath"
