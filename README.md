# PDF Paper Translator

一个可复制到不同文件夹使用的跨平台论文翻译应用包。

它通过 PDFMathTranslate 2.x、BabelDOC 和 DeepSeek 生成：

- 论文原文归档；
- 中英对照 PDF；
- 中文译文 PDF；
- 翻译 token 与费用报告；
- 论文元数据；
- 零 API 费用的运行前预检查与自测。
- 可选的 DeepSeek 带页码文本笔记。

## 1. 应用边界

应用包只包含启动器和安装器，不把约 850 MB 的 Python 运行环境及用户缓存塞进 ZIP。首次安装时会在应用内部创建 `.venv/`，BabelDOC 的模型与字体继续使用用户缓存。

应用不会保存 API Key，也不会把用户缓存打进分发包。

## 2. 安装

解压后进入应用目录，双击 `install.cmd`，或在 PowerShell 中执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

安装器需要：

- Windows PowerShell 5.1 或 PowerShell 7；
- `uv`；
- 可访问 Python 包仓库的网络连接。

安装完成后检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\doctor.ps1
```

`doctor.ps1` 只检查 `DEEPSEEK_API_KEY` 是否存在，不显示密钥。

## 3. 在任意文件夹自动处理

假设应用解压在：

```text
C:\Tools\pdf-paper-translator\
```

待翻译论文位于：

```text
D:\AI-Papers\example.pdf
```

日常使用一条命令完成去重、归档、全文翻译、机械校验和 DeepSeek 文本初读：

```powershell
C:\Tools\pdf-paper-translator\paperflow.cmd add "D:\AI-Papers\example.pdf"
```

默认在原 PDF 所在目录建立：

```text
D:\AI-Papers\papers\2026_Author_Short-Title\
```

只需要翻译时关闭自动笔记：

```powershell
C:\Tools\pdf-paper-translator\paperflow.cmd add `
  "D:\AI-Papers\example.pdf" --no-notes
```

批量处理文件夹中的所有 PDF：

```powershell
C:\Tools\pdf-paper-translator\paperflow.cmd batch "D:\AI-Papers\inbox"
```

名为 `inbox` 的目录默认输出到同级的 `papers/`。中断时子进程会被终止，状态记录为 `cancelled`，再次运行会复用已有文件和缓存。

`self-test.ps1` 和 `pdftranslate.ps1 -Preflight` 只用于安装、升级或翻译器开发阶段，不是每篇论文的日常步骤。

## 4. 输出结构

```text
papers/<论文标识>/
├── paper.pdf
├── paper_zh-CN.dual.pdf
├── paper_zh-CN.mono.pdf
├── metadata.yaml
├── translation-report.md
├── automation-status.json
└── notes.md
```

`notes.md` 是 DeepSeek 根据 PDF 可提取文本生成的初读笔记，不包含视觉检查。图、复杂表格和公式会标记为待核对；需要深入理解时再调用 Codex。

## 5. 指定统一论文库

如果不希望在原 PDF 旁边建立 `papers/`，可以指定统一档案根目录：

```powershell
paperflow.cmd add "D:\Downloads\paper.pdf" `
  --library-root "E:\PaperLibrary" `
  --paper-id "2026_Author_Short-Title"
```

输出将进入：

```text
E:\PaperLibrary\2026_Author_Short-Title\
```

## 6. 常用参数

`paperflow` 日常参数：

| 参数 | 说明 |
|---|---|
| `add <PDF>` | 完整处理一篇论文 |
| `batch <目录>` | 批量处理目录中的 PDF |
| `validate <论文目录>` | 只运行机械校验 |
| `--paper-id` | 手动指定论文档案目录名 |
| `--library-root` | 指定统一论文库 |
| `--no-translate` | 只归档或生成笔记，不翻译 |
| `--no-notes` | 不生成 DeepSeek 文本笔记 |
| `--force-translation` | 忽略现有译文重新翻译 |
| `--force-notes` | 覆盖并重新生成自动笔记 |

以下是底层 `pdftranslate` 排障参数：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `-PaperId` | 论文档案目录名 | 原 PDF 文件名 |
| `-LibraryRoot` | 统一论文库根目录 | 原 PDF 同级的 `papers/` |
| `-Preflight` | 零 API 调用地检查环境与路径 | 关闭 |
| `-Pages "2,5,8"` | 手动故障诊断时只翻译指定页，不属于正式工作流 | 全文 |
| `-OutputDirectory` | 特殊情况下覆盖输出目录 | 自动归档目录 |
| `-Model` | DeepSeek 模型名 | `deepseek-v4-flash` |
| `-Qps` | 每秒请求限制 | `2` |
| `-Workers` | 并发任务数 | `2` |
| `-SkipScannedDetection` | 跳过扫描件检测 | 关闭 |
| `-TranslateTableText` | 实验性表格文字翻译 | 关闭 |
| `-EnableAutoGlossary` | BabelDOC 自动术语抽取 | 关闭 |
| `-RuntimeDirectory` | 使用指定的已安装运行环境 | 应用内 `.venv/` |

## 7. 密钥和费用

API Key 只从当前进程或 Windows 用户级环境变量 `DEEPSEEK_API_KEY` 读取。它不会写入应用、日志、报告或 PDF。

每次成功运行后，`translation-report.md` 会记录：

- 模型和工具版本；
- 翻译页码与运行时间；
- 输入、输出和缓存 token；
- 按脚本价格快照计算的美元估算费用；
- 输出文件和人工质检清单。

费用是估算值，账户实际扣费以 DeepSeek 控制台为准。

版本 `0.2.1` 新增 `paperflow` 自动归档、批量处理、机械校验和 DeepSeek
文本笔记；`self-test.ps1` 与无写入的 `-Preflight` 保留为开发和安装检查。
它同时兼容 PDFMathTranslate 2.9.0 的 `Total Token Usage` 日志格式。

运行本地自测：

```powershell
powershell -ExecutionPolicy Bypass -File .\self-test.ps1
```

## 8. 更新与卸载

依赖版本固定在 `requirements.txt`。需要重装时：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Force
```

卸载时删除整个应用目录即可。论文档案默认位于原论文旁边或 `-LibraryRoot` 指定的位置，不会随应用目录删除。

## 9. 分发

在开发目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\pack.ps1
```

生成的 ZIP 位于 `dist/`。ZIP 不包含 `.venv/`、API Key、用户缓存或论文文件。

第三方许可说明见 `NOTICE.md`。


## macOS / Linux 原生入口

不需要 PowerShell。macOS Apple Silicon 已完成本机安装和离线验证；Intel Mac/Linux 尚未实机验证。需要 `uv`，安装说明：https://docs.astral.sh/uv/getting-started/installation/ 。

```bash
./install.sh
./doctor.sh
./paperflow.sh add /path/to/paper.pdf --library-root /path/to/workspace/papers
./paperflow.sh batch /path/to/workspace/inbox
```

在工作区根目录运行时使用 `./pdf-paper-translator/paperflow.sh`。安装器使用 Python 3.12 和锁定的三个直接依赖版本；间接依赖由 uv 解析。Windows 复制来的 `.venv` 会移至 `.venv-backup-*`，不删除旧环境。重复安装会补齐依赖。

密钥只从当前进程环境变量 `DEEPSEEK_API_KEY` 读取。可在 Mac 的 zsh 中交互输入，避免密钥进入命令历史：

```zsh
read -s 'DEEPSEEK_API_KEY?DeepSeek API key: '
export DEEPSEEK_API_KEY
printf '\n'
```

单独翻译和不调用 API 的检查：

```bash
./pdftranslate.sh /path/to/paper.pdf --library-root /path/to/papers
./pdftranslate.sh /path/to/paper.pdf --preflight
./paperflow.sh self-check
.venv/bin/python -m unittest discover -s . -p test_portable.py -v
```

选项采用 `--model`、`--pages`、`--qps`、`--workers`、`--output-directory`、`--skip-scanned-detection` 等形式；通过 `--help` 查看。`--preflight` 不创建档案、不调用 API，缺少密钥时仍能显示准备信息；`doctor.sh` 缺少密钥则返回非零状态。

支持 `PDF_PAPER_TRANSLATOR_RUNTIME` 指向其他原生虚拟环境。完整归档继续生成原文、译文、笔记、机械质检和状态文件；选页译文写入 `trials/`。失败运行也追加 token 报告，已有译文不会被失败运行覆盖。

模型核查（2026-09-16）：[DeepSeek 官方定价](https://api-docs.deepseek.com/quick_start/pricing/)说明 `deepseek-v4-flash` 仍可调用，但映射到 DeepSeek-V4.1-Flash；建议后续改用官方名称 `deepseek-flash`（可通过 `--model` 与 `--notes-model` 指定）。本次保留已有默认配置。Mac 翻译报告按峰谷价格提供估算范围；原有自动笔记和 Windows 入口的价格快照尚未升级，不应视为当前账单。

首轮真实翻译可能下载 BabelDOC 的字体/版面模型资源；离线测试不代表这些下载与 DeepSeek 网络调用已经验证。Windows 使用原有 `.cmd` / `.ps1` 入口。
