from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from .config import Settings


INDEX_GROUPS = (("collections", "favorites"), ("authors", "author_archive"))


def tokenize(text: str) -> list[str]:
    """Match the tokenization of the existing local BM25-style index."""
    text = text.lower()
    words = re.findall(r"[a-z0-9_+#.-]{2,}", text)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        words.extend(run[i : i + 2] for i in range(len(run) - 1))
        words.extend(run[i : i + 3] for i in range(len(run) - 2))
    return words


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except (ValueError, TypeError):
                    value = value.strip('"')
            metadata[key.strip()] = str(value).strip("'")
    return metadata, text[end + 5 :]


def answer_id_from_source(source: str) -> str | None:
    parsed = urlparse(source)
    if parsed.netloc not in {"www.zhihu.com", "zhihu.com"}:
        return None
    match = re.search(r"/answer/(\d+)(?:/)?$", parsed.path)
    return match.group(1) if match else None


@dataclass(frozen=True)
class Article:
    answer_id: str
    title: str
    author: str
    source: str
    file: Path
    collection: str


class Corpus:
    def __init__(self, settings: Settings):
        self.root = settings.project_root
        self.source_root = PureWindowsPath(settings.source_root)
        self.data_dir = settings.data_dir
        self.articles: dict[str, Article] = {}
        self.chunks: list[tuple[dict[str, Any], Article]] = []
        self._load_indexes()

    def _resolve_file(self, raw: str) -> Path:
        stored = PureWindowsPath(raw)
        try:
            relative = stored.relative_to(self.source_root)
        except ValueError as exc:
            raise ValueError("Index contains a file outside the configured source root") from exc
        candidate = (self.root / Path(*relative.parts)).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError("Index contains an unsafe file path")
        return candidate

    def _load_indexes(self) -> None:
        indexes = ((path, collection) for directory, collection in INDEX_GROUPS
                   for path in sorted(self.root.glob(f"{directory}/*/rag_index.json")) if path.is_file())
        for path, collection in indexes:
            data = json.loads(path.read_text(encoding="utf-8"))
            for chunk in data["chunks"]:
                source = str(chunk.get("source", ""))
                answer_id = answer_id_from_source(source)
                if not answer_id:
                    continue
                article = self.articles.get(answer_id)
                if article is None:
                    file = self._resolve_file(str(chunk["file"]))
                    if not file.is_file():
                        continue
                    metadata, _ = frontmatter(file.read_text(encoding="utf-8", errors="replace"))
                    article = Article(
                        answer_id=answer_id,
                        title=metadata.get("title") or str(chunk["title"]),
                        author=metadata.get("author", ""),
                        source=source,
                        file=file,
                        collection=collection,
                    )
                    self.articles[answer_id] = article
                self.chunks.append((chunk, article))

        self._load_overlay()
        if not self.chunks:
            raise ValueError("No readable indexed answers found")
        df: Counter[str] = Counter()
        for chunk, _ in self.chunks:
            df.update(chunk["term_counts"].keys())
        n = len(self.chunks)
        self.idf = {term: math.log(1 + (n - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}
        self.avg_len = sum(chunk["length"] for chunk, _ in self.chunks) / n

    def _load_overlay(self) -> None:
        if not self.data_dir.is_dir():
            return
        for file in sorted(self.data_dir.glob("*.md")):
            metadata, body = frontmatter(file.read_text(encoding="utf-8", errors="replace"))
            source = metadata.get("source", "")
            answer_id = answer_id_from_source(source)
            if not answer_id or answer_id in self.articles or file.stem != answer_id:
                continue
            article = Article(
                answer_id=answer_id,
                title=metadata.get("title", answer_id),
                author=metadata.get("author", ""),
                source=source,
                file=file,
                collection="specified_answer",
            )
            self.articles[answer_id] = article
            for chunk_index, chunk_text in enumerate(_split_text(body), start=1):
                counts = Counter(tokenize(f"{article.title}\n{chunk_text}"))
                self.chunks.append(
                    (
                        {
                            "id": f"manual-{answer_id}-{chunk_index}",
                            "file": str(file),
                            "title": article.title,
                            "source": article.source,
                            "chunk_index": chunk_index,
                            "text": chunk_text,
                            "term_counts": dict(counts),
                            "length": max(1, sum(counts.values())),
                        },
                        article,
                    )
                )

    def _score(self, terms: Counter[str], chunk: dict[str, Any]) -> float:
        counts = chunk["term_counts"]
        length = chunk["length"]
        score = 0.0
        for term, query_count in terms.items():
            tf = counts.get(term, 0)
            if tf:
                denom = tf + 1.5 * (1 - 0.75 + 0.75 * length / self.avg_len)
                score += query_count * self.idf.get(term, 0.0) * tf * 2.5 / denom
        return score

    def search(self, query: str, *, author: str = "", limit: int = 5) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        terms = Counter(tokenize(query[:300]))
        if not terms:
            return []
        scored: list[tuple[float, dict[str, Any], Article]] = []
        for chunk, article in self.chunks:
            if author and author.casefold() not in article.author.casefold():
                continue
            score = self._score(terms, chunk)
            if score > 0:
                scored.append((score, chunk, article))
        scored.sort(key=lambda item: item[0], reverse=True)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for score, chunk, article in scored:
            if article.answer_id in seen:
                continue
            seen.add(article.answer_id)
            results.append(
                {
                    "answer_id": article.answer_id,
                    "title": article.title,
                    "author": article.author,
                    "source": article.source,
                    "score": round(score, 3),
                    "snippet": re.sub(r"\s+", " ", chunk["text"]).strip()[:550],
                }
            )
            if len(results) >= max(1, min(limit, 8)):
                break
        return results

    def read(self, answer_id: str, *, start: int = 0, max_chars: int = 7000) -> dict[str, Any]:
        if not re.fullmatch(r"\d{1,24}", answer_id):
            raise ValueError("answer_id must be a numeric ID from search results")
        article = self.articles.get(answer_id)
        if article is None:
            raise KeyError("answer_id is not in the local index")
        _, body = frontmatter(article.file.read_text(encoding="utf-8", errors="replace"))
        start = max(0, min(start, len(body)))
        max_chars = max(500, min(max_chars, 7000))
        end = min(len(body), start + max_chars)
        return {
            "answer_id": article.answer_id,
            "title": article.title,
            "author": article.author,
            "source": article.source,
            "start": start,
            "end": end,
            "total_chars": len(body),
            "has_more": end < len(body),
            "text": body[start:end],
        }


def _split_text(text: str, size: int = 1200, overlap: int = 160) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(paragraph) <= size:
            current = paragraph
        else:
            step = size - overlap
            for start in range(0, len(paragraph), step):
                chunks.append(paragraph[start : start + size])
    if current:
        chunks.append(current)
    return chunks
