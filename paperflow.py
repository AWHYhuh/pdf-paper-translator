#!/usr/bin/env python3
"""Automate paper archiving, translation, mechanical validation, and text notes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fitz


DEFAULT_MODEL = "deepseek-v4-flash"
PRICE_TABLE = {
    "deepseek-v4-flash": (0.0028, 0.14, 0.28),
    "deepseek-v4-pro": (0.003625, 0.435, 0.87),
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def extract_first_page_metadata(document: fitz.Document) -> tuple[str, list[str]]:
    raw_blocks = [
        block[4].strip()
        for block in document[0].get_text("blocks")
        if block[4].strip()
    ]
    if not raw_blocks:
        return "", []
    title = clean_text(raw_blocks[0])
    authors: list[str] = []
    for block in raw_blocks[1:5]:
        if re.search(
            r"\b(Abstract|University|Institute|School|Laboratory|Microsoft|Google|Meta)\b",
            block,
            flags=re.IGNORECASE,
        ) or "@" in block:
            break
        for part in re.split(r"[\r\n,]+|\band\b", block):
            part = re.sub(r"[\u2660-\u2667*†‡∗]+", "", part)
            name = clean_text(part)
            words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ’'.-]+", name)
            if (
                2 <= len(name) <= 80
                and "@" not in name
                and 2 <= len(words) <= 7
                and len("".join(words)) >= max(2, int(len(name) * 0.6))
            ):
                authors.append(name)
    return title, authors


def inspect_pdf(path: Path) -> dict[str, Any]:
    try:
        document = fitz.open(path)
    except Exception as error:
        raise RuntimeError(f"Cannot open PDF: {error}") from error
    try:
        if document.needs_pass:
            raise RuntimeError("The PDF is password protected.")
        if document.page_count < 1:
            raise RuntimeError("The PDF has no pages.")
        embedded = document.metadata or {}
        title = clean_text(embedded.get("title", ""))
        raw_author = clean_text(embedded.get("author", ""))
        authors = [
            clean_text(item)
            for item in re.split(r";|,|\band\b", raw_author)
            if clean_text(item)
        ]
        fallback_title, fallback_authors = extract_first_page_metadata(document)
        if not title or title.lower() in {"untitled", "arxiv"}:
            title = fallback_title
        if not authors:
            authors = fallback_authors
        creation = embedded.get("creationDate", "")
        year_match = re.search(r"(19|20)\d{2}", creation)
        year = int(year_match.group(0)) if year_match else None
        pages = []
        blank_pages = []
        for index, page in enumerate(document):
            text = page.get_text("text").strip()
            pages.append(text)
            if len(text) < 20:
                blank_pages.append(index + 1)
        return {
            "title": title,
            "authors": authors,
            "year": year,
            "page_count": document.page_count,
            "blank_pages": blank_pages,
            "pages": pages,
            "sha256": sha256_file(path),
        }
    finally:
        document.close()


def arxiv_metadata(path: Path) -> dict[str, Any]:
    match = re.search(r"(?<!\d)(\d{4}\.\d{4,5})(v\d+)?", path.stem)
    if not match:
        return {}
    identifier = match.group(1)
    prefix = int(identifier[:2])
    year = 2000 + prefix if prefix < 90 else 1900 + prefix
    version = match.group(2) or ""
    return {
        "year": year,
        "venue": f"arXiv:{identifier}{version}",
        "source_url": f"https://arxiv.org/abs/{identifier}",
    }


def safe_identifier(metadata: dict[str, Any], explicit: str | None) -> str:
    if explicit:
        candidate = explicit
    else:
        year = metadata.get("year") or "UnknownYear"
        authors = metadata.get("authors") or ["UnknownAuthor"]
        surname_parts = re.findall(r"[A-Za-z0-9]+", authors[0])
        surname = surname_parts[-1] if surname_parts else "UnknownAuthor"
        title_words = re.findall(r"[A-Za-z0-9]+", metadata.get("title", ""))
        stop = {"a", "an", "the", "of", "for", "to", "and", "in", "on", "with"}
        short_words = [word for word in title_words if word.lower() not in stop][:8]
        short_title = "-".join(short_words) or "Paper"
        candidate = f"{year}_{surname}_{short_title}"
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip(".-_")
    if not candidate:
        raise RuntimeError("Could not create a safe paper identifier.")
    return candidate[:120]


def find_duplicate(library: Path, digest: str) -> Path | None:
    if not library.exists():
        return None
    for candidate in library.glob("*/paper.pdf"):
        try:
            if sha256_file(candidate) == digest:
                return candidate.parent
        except OSError:
            continue
    return None


def write_metadata(
    path: Path,
    metadata: dict[str, Any],
    original_name: str,
    status: str,
) -> None:
    if path.exists():
        content = path.read_text(encoding="utf-8")
        replacements = {
            "translation_status": status,
            "sha256": metadata["sha256"],
            "page_count": str(metadata["page_count"]),
        }
        for key, value in replacements.items():
            rendered = yaml_quote(value) if key != "page_count" else value
            if re.search(rf"(?m)^{re.escape(key)}:", content):
                content = re.sub(
                    rf"(?m)^{re.escape(key)}:.*$", f"{key}: {rendered}", content
                )
            else:
                content = content.rstrip() + f"\n{key}: {rendered}\n"
        atomic_write(path, content)
        return

    authors = metadata.get("authors") or []
    author_lines = "\n".join(f"  - {yaml_quote(author)}" for author in authors)
    if not author_lines:
        author_lines = "  []"
    content = f"""title: {yaml_quote(metadata.get("title", ""))}
authors:
{author_lines}
year: {metadata.get("year") or ""}
venue: {yaml_quote(metadata.get("venue", ""))}
source_url: {yaml_quote(metadata.get("source_url", ""))}
original_file: {yaml_quote(original_name)}
archived_file: "paper.pdf"
sha256: {yaml_quote(metadata["sha256"])}
page_count: {metadata["page_count"]}
translation_status: {yaml_quote(status)}
reading_status: "unread"
archived_at: {yaml_quote(datetime.now().date().isoformat())}
"""
    atomic_write(path, content)


def update_yaml_field(path: Path, key: str, value: str) -> None:
    if not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    rendered = yaml_quote(value)
    if re.search(rf"(?m)^{re.escape(key)}:", content):
        content = re.sub(
            rf"(?m)^{re.escape(key)}:.*$", f"{key}: {rendered}", content
        )
    else:
        content = content.rstrip() + f"\n{key}: {rendered}\n"
    atomic_write(path, content)


def archive_pdf(
    source: Path, library: Path, paper_id: str | None
) -> tuple[Path, dict[str, Any], bool]:
    source_info = inspect_pdf(source)
    source_info.update(arxiv_metadata(source))
    duplicate = find_duplicate(library, source_info["sha256"])
    if duplicate:
        return duplicate, source_info, False
    identifier = safe_identifier(source_info, paper_id)
    paper_dir = library / identifier
    archive = paper_dir / "paper.pdf"
    if archive.exists() and sha256_file(archive) != source_info["sha256"]:
        raise RuntimeError(f"Archive collision: {paper_dir}")
    paper_dir.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        shutil.copy2(source, archive)
    write_metadata(
        paper_dir / "metadata.yaml", source_info, source.name, "not_started"
    )
    return paper_dir, source_info, True


def pdf_metrics(path: Path) -> dict[str, Any]:
    info = inspect_pdf(path)
    text = "\n".join(info.pop("pages"))
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    visible = len(re.findall(r"\S", text))
    return {
        **info,
        "file_size": path.stat().st_size,
        "text_characters": visible,
        "chinese_characters": chinese,
        "chinese_ratio": round(chinese / visible, 4) if visible else 0.0,
    }


def validate_archive(paper_dir: Path) -> dict[str, Any]:
    required = {
        "source": paper_dir / "paper.pdf",
        "mono": paper_dir / "paper_zh-CN.mono.pdf",
        "dual": paper_dir / "paper_zh-CN.dual.pdf",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    result: dict[str, Any] = {
        "checked_at": now_iso(),
        "status": "failed" if missing else "passed",
        "missing": missing,
        "files": {},
        "warnings": [],
    }
    if missing:
        return result
    for name, path in required.items():
        result["files"][name] = pdf_metrics(path)
    source_pages = result["files"]["source"]["page_count"]
    for name in ("mono", "dual"):
        if result["files"][name]["page_count"] != source_pages:
            result["status"] = "failed"
            result["warnings"].append(f"{name} page count differs from source")
        if result["files"][name]["blank_pages"]:
            result["status"] = "failed"
            result["warnings"].append(f"{name} contains near-empty pages")
    if result["files"]["mono"]["chinese_ratio"] < 0.08:
        result["status"] = "failed"
        result["warnings"].append("mono PDF has an unusually low Chinese-text ratio")
    return result


def update_validation_report(paper_dir: Path, result: dict[str, Any]) -> None:
    report = paper_dir / "translation-report.md"
    if not report.exists():
        return
    begin = "<!-- paperflow-validation:start -->"
    end = "<!-- paperflow-validation:end -->"
    files = result.get("files", {})
    source_pages = files.get("source", {}).get("page_count", "unknown")
    mono_ratio = files.get("mono", {}).get("chinese_ratio", 0)
    warnings = result.get("warnings") or []
    warning_text = "\n".join(f"- {warning}" for warning in warnings) or "- None"
    section = f"""{begin}

## Automated mechanical validation

- Status: `{result["status"]}`
- Checked at: `{result["checked_at"]}`
- Source/output page count: `{source_pages}`
- Chinese character ratio in mono PDF: `{mono_ratio:.2%}`
- This check verifies file integrity and text coverage, not semantic fidelity or visual correctness.

Warnings:

{warning_text}

{end}"""
    content = report.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    content = pattern.sub(section, content) if pattern.search(content) else (
        content.rstrip() + "\n\n" + section + "\n"
    )
    atomic_write(report, content)


def api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set.")
    return key


def deepseek_call(
    *,
    key: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    retries: int = 3,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.1,
        "stream": False,
        "thinking": {"type": "disabled"},
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                data = json.loads(response.read().decode("utf-8"))
            choice = data["choices"][0]
            content = choice["message"].get("content", "").strip()
            if not content or choice.get("finish_reason") == "length":
                raise RuntimeError("DeepSeek returned empty or truncated content.")
            return {"content": content, "usage": data.get("usage", {})}
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"DeepSeek notes request failed: {last_error}")


def usage_cost(model: str, usage: dict[str, Any]) -> float:
    prices = PRICE_TABLE.get(model)
    if not prices:
        return 0.0
    hit = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
    miss = int(usage.get("prompt_cache_miss_tokens", 0) or 0)
    prompt = int(usage.get("prompt_tokens", 0) or 0)
    if not hit and not miss:
        miss = prompt
    output = int(usage.get("completion_tokens", 0) or 0)
    return (hit * prices[0] + miss * prices[1] + output * prices[2]) / 1_000_000


def page_chunks(pages: list[str], max_chars: int = 24000) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for number, text in enumerate(pages, start=1):
        marked = f"[[PDF p.{number}]]\n{text.strip()}"
        if current and size + len(marked) > max_chars:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(marked)
        size += len(marked)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def strip_fence(content: str) -> str:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:markdown)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return content.strip()


def generate_notes(
    paper_dir: Path,
    metadata: dict[str, Any],
    model: str,
    force: bool,
    request_function: Any = deepseek_call,
) -> dict[str, Any]:
    notes_path = paper_dir / "notes.md"
    if notes_path.exists() and not force:
        return {"status": "skipped_existing", "estimated_cost_usd": 0.0}
    source = inspect_pdf(paper_dir / "paper.pdf")
    chunks = page_chunks(source["pages"])
    cache_root = Path(__file__).parent / ".cache" / "notes" / source["sha256"]
    cache_root.mkdir(parents=True, exist_ok=True)
    key = api_key() if request_function is deepseek_call else "self-test-key"
    total_usage: dict[str, int] = {}
    analyses: list[str] = []
    system = (
        "You analyze academic AI papers from extracted text only. Use Chinese. "
        "Separate paper statements from explanations and criticism. Every factual "
        "claim must cite a supplied [[PDF p.N]] marker. Never infer content from "
        "figures, complex tables, or malformed formulas; mark it as 待视觉核对."
    )
    for index, chunk in enumerate(chunks, start=1):
        cache = cache_root / f"chunk-{index:04d}.json"
        if cache.exists():
            result = json.loads(cache.read_text(encoding="utf-8"))
        else:
            result = request_function(
                key=key,
                model=model,
                system=system,
                user=(
                    "Extract only evidence useful for a structured paper note: "
                    "problem, contribution, method, data, experiments, numerical "
                    "results, limitations, formulas/tables/figures needing visual "
                    "review, and 3-8 technical terms. Do not write a final note.\n\n"
                    + chunk
                ),
                max_tokens=5000,
            )
            atomic_json(cache, result)
        analyses.append(result["content"])
        for name, value in result.get("usage", {}).items():
            if isinstance(value, int):
                total_usage[name] = total_usage.get(name, 0) + value

    synthesis = request_function(
        key=key,
        model=model,
        system=system,
        user=(
            "Create a concise Markdown paper note from the evidence summaries below. "
            "Use these numbered sections: 基本信息, 一句话总结, 阅读结论, 研究背景与问题, "
            "核心贡献, 方法总览, 模型结构与数据流, 关键公式, 数据集与实验设置, "
            "实验结果, 消融实验, 优点, 局限与可疑之处, 复现信息, 可延伸方向, "
            "我的疑问, 专业术语与生词, 重要原文定位. Use labels 论文陈述, 助理解释, "
            "合理推断, 批判性评价, 待核实. State 论文未说明 where appropriate. "
            "Do not include YAML front matter or a top-level title.\n\n"
            + "\n\n--- CHUNK EVIDENCE ---\n\n".join(analyses)
        ),
        max_tokens=12000,
    )
    for name, value in synthesis.get("usage", {}).items():
        if isinstance(value, int):
            total_usage[name] = total_usage.get(name, 0) + value
    title = metadata.get("title") or source.get("title") or paper_dir.name
    front = f"""---
title: {yaml_quote(title)}
reading_status: "DeepSeek 文本初读"
visual_review: "not_performed"
source_file: "paper.pdf"
created: {yaml_quote(datetime.now().date().isoformat())}
---

# {title}

> 本笔记由 DeepSeek 根据 PDF 可提取文本自动生成，未进行页面视觉检查。图、表、公式及解析异常内容必须回看原文。

"""
    atomic_write(notes_path, front + strip_fence(synthesis["content"]) + "\n")
    cost = usage_cost(model, total_usage)
    return {
        "status": "completed",
        "model": model,
        "chunks": len(chunks),
        "usage": total_usage,
        "estimated_cost_usd": round(cost, 8),
    }


def run_translation(
    paper_dir: Path,
    model: str,
    force: bool,
    skip_scanned_detection: bool,
) -> str:
    mono = paper_dir / "paper_zh-CN.mono.pdf"
    dual = paper_dir / "paper_zh-CN.dual.pdf"
    if mono.exists() and dual.exists() and not force:
        return "skipped_existing"
    if os.name == "nt":
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", str(Path(__file__).parent / "pdftranslate.ps1"),
                   str(paper_dir / "paper.pdf"), "-Model", model]
        if skip_scanned_detection:
            command.append("-SkipScannedDetection")
    else:
        command = [sys.executable, str(Path(__file__).parent / "pdftranslate.py"),
                   str(paper_dir / "paper.pdf"), "--model", model]
        if skip_scanned_detection:
            command.append("--skip-scanned-detection")
    process = subprocess.Popen(command)
    try:
        return_code = process.wait()
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise
    if return_code in {130, -1073741510, 3221225786}:
        raise KeyboardInterrupt
    if return_code:
        raise RuntimeError(f"Translator exited with code {return_code}.")
    return "completed"


def append_notes_cost(paper_dir: Path, notes_result: dict[str, Any]) -> None:
    if notes_result.get("status") != "completed":
        return
    report = paper_dir / "translation-report.md"
    if not report.exists():
        return
    begin = "<!-- paperflow-notes-cost:start -->"
    end = "<!-- paperflow-notes-cost:end -->"
    usage = notes_result.get("usage", {})
    section = f"""

{begin}
## DeepSeek text-note generation

- Model: `{notes_result["model"]}`
- Text chunks: `{notes_result["chunks"]}`
- Prompt tokens: `{int(usage.get("prompt_tokens", 0)):,}`
- Completion tokens: `{int(usage.get("completion_tokens", 0)):,}`
- Estimated cost (USD): `${notes_result["estimated_cost_usd"]:.6f}`
- Visual review: not performed
{end}
"""
    content = report.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    content = pattern.sub(section.strip(), content) if pattern.search(content) else (
        content.rstrip() + section
    )
    atomic_write(report, content.rstrip() + "\n")


def process_one(args: argparse.Namespace, source: Path) -> dict[str, Any]:
    source = source.resolve()
    if not source.exists() or source.suffix.lower() != ".pdf":
        raise RuntimeError(f"Not a PDF file: {source}")
    library = (
        Path(args.library_root).resolve()
        if args.library_root
        else (
            source.parent.parent / "papers"
            if source.parent.name.lower() == "inbox"
            else source.parent / "papers"
        )
    )
    paper_dir, metadata, created = archive_pdf(
        source, library, getattr(args, "paper_id", None)
    )
    translation_status = "disabled"
    validation: dict[str, Any] = {"status": "not_run"}
    notes_result: dict[str, Any] = {"status": "disabled"}
    status = {
        "schema_version": 1,
        "updated_at": now_iso(),
        "source": str(source),
        "paper_directory": str(paper_dir),
        "archive_created": created,
        "translation": translation_status,
        "validation": validation,
        "notes": notes_result,
    }
    status_path = paper_dir / "automation-status.json"
    atomic_json(status_path, status)
    try:
        if not args.no_translate:
            translation_status = "running"
            status.update(
                {"updated_at": now_iso(), "translation": translation_status}
            )
            atomic_json(status_path, status)
            print(f"[TRANSLATE] {source.name}", flush=True)
            translation_status = run_translation(
                paper_dir,
                args.model,
                args.force_translation,
                args.skip_scanned_detection,
            )
            validation = validate_archive(paper_dir)
            update_validation_report(paper_dir, validation)
            if validation["status"] != "passed":
                raise RuntimeError(
                    "Translation completed but mechanical validation failed: "
                    + "; ".join(
                        validation.get("warnings") or validation.get("missing")
                    )
                )
            write_metadata(
                paper_dir / "metadata.yaml", metadata, source.name, "completed"
            )
        if not args.no_notes:
            print(f"[NOTES] {source.name}", flush=True)
            notes_result = generate_notes(
                paper_dir, metadata, args.notes_model, args.force_notes
            )
            append_notes_cost(paper_dir, notes_result)
            if notes_result.get("status") == "completed":
                update_yaml_field(
                    paper_dir / "metadata.yaml",
                    "reading_status",
                    "DeepSeek 文本初读",
                )
        status.update(
            {
                "updated_at": now_iso(),
                "translation": translation_status,
                "validation": validation,
                "notes": notes_result,
            }
        )
        atomic_json(status_path, status)
    except KeyboardInterrupt:
        status.update(
            {
                "updated_at": now_iso(),
                "translation": (
                    "cancelled"
                    if translation_status == "running"
                    else translation_status
                ),
                "validation": validation,
                "notes": notes_result,
                "error": "cancelled_by_user",
            }
        )
        atomic_json(status_path, status)
        raise
    except Exception as error:
        status.update(
            {
                "updated_at": now_iso(),
                "translation": translation_status,
                "validation": validation,
                "notes": notes_result,
                "error": str(error),
            }
        )
        atomic_json(status_path, status)
        raise
    return status


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="paperflow")
    sub = root.add_subparsers(dest="command", required=True)

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--library-root")
        target.add_argument("--model", default=DEFAULT_MODEL)
        target.add_argument("--notes-model", default=DEFAULT_MODEL)
        target.add_argument("--no-translate", action="store_true")
        target.add_argument("--no-notes", action="store_true")
        target.add_argument("--force-translation", action="store_true")
        target.add_argument("--force-notes", action="store_true")
        target.add_argument("--skip-scanned-detection", action="store_true")

    add = sub.add_parser("add", help="Archive and process one PDF")
    add.add_argument("pdf")
    add.add_argument("--paper-id")
    common(add)

    batch = sub.add_parser("batch", help="Process every PDF in a folder")
    batch.add_argument("folder")
    common(batch)

    validate = sub.add_parser("validate", help="Mechanically validate an archive")
    validate.add_argument("paper_directory")
    sub.add_parser("self-check", help=argparse.SUPPRESS)
    return root


def internal_self_check() -> None:
    with tempfile.TemporaryDirectory(prefix="paperflow-self-check-") as temporary:
        root = Path(temporary)
        source = root / "2401.00001v1.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "A Small Paperflow Test")
        page.insert_text((72, 96), "Ada Researcher")
        page.insert_text((72, 140), "Abstract")
        page.insert_text(
            (72, 165),
            "This paper introduces a deterministic workflow for paper processing.",
        )
        document.save(source)
        document.close()
        paper_dir, metadata, created = archive_pdf(source, root / "papers", None)
        if not created or metadata["page_count"] != 1:
            raise RuntimeError("Archive self-check failed.")

        def fake_request(**kwargs: Any) -> dict[str, Any]:
            return {
                "content": (
                    "## 基本信息\n\n- 论文陈述：自动化测试。[PDF p.1]\n\n"
                    "## 重要原文定位\n\n- PDF p.1"
                ),
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }

        result = generate_notes(
            paper_dir,
            metadata,
            DEFAULT_MODEL,
            False,
            request_function=fake_request,
        )
        notes = (paper_dir / "notes.md").read_text(encoding="utf-8")
        if result["status"] != "completed" or "visual_review: \"not_performed\"" not in notes:
            raise RuntimeError("Notes self-check failed.")

        original_runner = globals()["run_translation"]
        try:
            def cancelled_runner(*args: Any, **kwargs: Any) -> str:
                raise KeyboardInterrupt

            globals()["run_translation"] = cancelled_runner
            cancel_source = root / "cancel.pdf"
            shutil.copy2(source, cancel_source)
            cancel_args = SimpleNamespace(
                library_root=str(root / "cancel-library"),
                paper_id=None,
                no_translate=False,
                no_notes=True,
                model=DEFAULT_MODEL,
                notes_model=DEFAULT_MODEL,
                force_translation=False,
                force_notes=False,
                skip_scanned_detection=False,
            )
            try:
                process_one(cancel_args, cancel_source)
                raise RuntimeError("Cancellation self-check did not interrupt.")
            except KeyboardInterrupt:
                statuses = list(
                    (root / "cancel-library").glob("*/automation-status.json")
                )
                if len(statuses) != 1:
                    raise RuntimeError("Cancellation status was not written.")
                cancelled = json.loads(statuses[0].read_text(encoding="utf-8"))
                if cancelled.get("translation") != "cancelled":
                    raise RuntimeError("Cancellation status is incorrect.")
        finally:
            globals()["run_translation"] = original_runner


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "self-check":
            internal_self_check()
            print("Paperflow internal self-check passed. No API call was made.")
            return 0
        if args.command == "validate":
            paper_dir = Path(args.paper_directory).resolve()
            result = validate_archive(paper_dir)
            status_path = paper_dir / "automation-status.json"
            existing: dict[str, Any] = {}
            if status_path.exists():
                try:
                    existing = json.loads(status_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    existing = {}
            existing.update({
                "schema_version": 1,
                "updated_at": now_iso(),
                "paper_directory": str(paper_dir),
                "validation": result,
            })
            if result["status"] == "passed":
                existing["translation"] = "existing_completed"
            if (
                (paper_dir / "notes.md").exists()
                and existing.get("notes", {}).get("status") in {None, "disabled"}
            ):
                existing["notes"] = {"status": "existing"}
            atomic_json(status_path, existing)
            update_validation_report(paper_dir, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "passed" else 1
        if args.command == "add":
            status = process_one(args, Path(args.pdf))
            print(f"Paper archive: {status['paper_directory']}")
            print(f"Translation: {status['translation']}")
            print(f"Validation: {status['validation']['status']}")
            print(f"Notes: {status['notes']['status']}")
            return 0

        folder = Path(args.folder).resolve()
        failures = 0
        for pdf in sorted(folder.glob("*.pdf")):
            try:
                print(f"[START] {pdf.name}", flush=True)
                status = process_one(args, pdf)
                print(f"[OK] {pdf.name} -> {status['paper_directory']}")
            except KeyboardInterrupt:
                raise
            except Exception as error:
                failures += 1
                print(f"[FAIL] {pdf.name}: {error}", file=sys.stderr)
        return 1 if failures else 0
    except KeyboardInterrupt:
        print(
            "CANCELLED: stopped by user. Partial files were preserved.",
            file=sys.stderr,
        )
        return 130
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
