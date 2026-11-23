from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .agent import ZhihuAgent


@dataclass(frozen=True)
class EvalCase:
    number: int
    kind: str
    question: str
    gold_urls: tuple[str, ...]
    expect_abstain: bool


def load_cases(path: Path) -> list[EvalCase]:
    text = path.read_text(encoding="utf-8")
    source_urls = dict(
        re.findall(r"^- \[([A-Z])\].*?\((https://www\.zhihu\.com/answer/\d+)\)", text, re.MULTILINE)
    )
    cases: list[EvalCase] = []
    for line in text.splitlines():
        if not re.match(r"^\| \d\d \|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            raise ValueError("Invalid evaluation table row")
        number, kind, question, _, refs = cells
        labels = re.findall(r"\[([A-Z])\]", refs)
        cases.append(
            EvalCase(
                number=int(number),
                kind=kind,
                question=question,
                gold_urls=tuple(source_urls[label] for label in labels),
                expect_abstain=kind == "资料不足",
            )
        )
    if len(cases) != 20 or [case.number for case in cases] != list(range(1, 21)):
        raise ValueError("Expected exactly 20 numbered evaluation cases")
    return cases


def run_cases(agent: ZhihuAgent, cases: list[EvalCase], output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    metrics = {"total": 0, "source_recall_hits": 0, "abstain_hits": 0, "citation_warnings": 0}
    with output.open("w", encoding="utf-8") as file:
        for case in cases:
            result = agent.ask(case.question)
            urls = {source["url"] for source in result.sources}
            source_hit = bool(case.gold_urls) and set(case.gold_urls).issubset(urls)
            abstain_hit = case.expect_abstain and bool(
                re.search(r"资料不足|无法确认|不能确认|没有足够|未能确认", result.answer)
            )
            record = {
                "case": case.number,
                "kind": case.kind,
                "question": case.question,
                "gold_urls": case.gold_urls,
                "source_hit": source_hit,
                "abstain_hit": abstain_hit,
                **result.as_dict(),
            }
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
            file.flush()
            metrics["total"] += 1
            metrics["source_recall_hits"] += int(source_hit)
            metrics["abstain_hits"] += int(abstain_hit)
            metrics["citation_warnings"] += int(result.citation_warning)
    return metrics
