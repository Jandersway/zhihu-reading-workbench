from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .config import Settings
from .corpus import Corpus, answer_id_from_source


DEFAULT_CLI = "zhihu"


def save_browse_result(url: str, result: dict, data_dir: Path) -> Path:
    answer_id = answer_id_from_source(url)
    if not answer_id:
        raise ValueError("Provide one zhihu.com answer URL with a numeric ID")
    metadata = result.get("metadata") or {}
    content = result.get("content_md")
    if not isinstance(metadata, dict) or str(metadata.get("id", "")) != answer_id:
        raise ValueError("CLI returned an answer ID different from the requested URL")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("CLI returned no answer content")
    title = str(metadata.get("title") or "untitled")
    author = str(metadata.get("author") or "unknown")
    created = str(metadata.get("created") or "unknown")
    canonical = f"https://www.zhihu.com/answer/{answer_id}"
    document = (
        "---\n"
        f"title: {json.dumps(title, ensure_ascii=False)}\n"
        f"author: {json.dumps(author, ensure_ascii=False)}\n"
        f"created: {json.dumps(created, ensure_ascii=False)}\n"
        f"source: {canonical}\n"
        "---\n\n"
        f"{content.strip()}\n"
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    output = data_dir / f"{answer_id}.md"
    if output.exists():
        raise FileExistsError(f"Answer {answer_id} has already been saved")
    output.write_text(document, encoding="utf-8")
    return output


def ingest_answer(url: str, settings: Settings) -> Path:
    answer_id = answer_id_from_source(url)
    if not answer_id:
        raise ValueError("Provide one zhihu.com answer URL with a numeric ID")
    if not re.fullmatch(r"\d{1,24}", answer_id):
        raise ValueError("Invalid answer ID")
    if (settings.data_dir / f"{answer_id}.md").exists():
        raise FileExistsError(f"Answer {answer_id} has already been saved")
    if answer_id in Corpus(settings).articles:
        raise FileExistsError(f"Answer {answer_id} is already present in the local index")
    cli = os.getenv("ZHIHU_CLI_PATH", DEFAULT_CLI)
    try:
        process = subprocess.run(
            [cli, "browse", "answer", url, "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Zhihu CLI could not complete: {type(exc).__name__}") from None
    if process.returncode != 0:
        raise RuntimeError(f"Zhihu CLI returned exit code {process.returncode}; details omitted")
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError:
        raise RuntimeError("Zhihu CLI did not return valid JSON") from None
    return save_browse_result(url, result, settings.data_dir)
