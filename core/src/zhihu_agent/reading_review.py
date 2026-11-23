"""Human feedback interface. No model API and no remote account mutations."""
from __future__ import annotations

import json

from .recommend import ReadingStore, render


def rate(store: ReadingStore, number: int, verdict: str, *, run_id="latest", note="", scope="article", topic=""):
    report = store.report_by_id(run_id)
    items = report["recommendations"]
    if type(number) is not int or not 1 <= number <= len(items):
        raise ValueError("Number is not in the selected report")
    item = items[number - 1]
    store.feedback(item["candidate_id"], verdict, note, scope=scope, topic=topic, run_id=report["run_id"])
    return {"run_id": report["run_id"], "candidate_id": item["candidate_id"], "title": item["title"],
            "verdict": verdict, "scope": scope, "note": note}


def review(store: ReadingStore, run_id="latest", *, read_input=input, write=print):
    report = store.report_by_id(run_id)
    write(render(report))
    write("\n逐条评价：1 喜欢 / 2 不喜欢 / 3 太浅 / 4 看过 / 9 撤销旧评价 / 回车跳过 / q 结束")
    choices = {"1": "like", "2": "dislike", "3": "too_shallow", "4": "seen", "9": "clear"}
    try:
        for number, item in enumerate(report["recommendations"], 1):
            while True:
                choice = read_input(f"第 {number} 条《{item['title']}》：").strip().lower()
                if choice in choices or choice in {"", "q"}:
                    break
                write("请输入 1、2、3、4、9，或回车跳过。")
            if choice == "q":
                break
            if not choice:
                continue
            verdict = choices[choice]
            note = "" if verdict in {"seen", "clear"} else read_input("原因/想看什么（可留空）：").strip()
            # Interactive ratings are deliberately about this item. Topic-wide
            # changes require the explicit --scope topic --topic CLI options.
            rate(store, number, verdict, run_id=report["run_id"], note=note)
            write("已记录；默认只评价这篇，最新评价覆盖旧评价的影响。")
    except (EOFError, KeyboardInterrupt):
        write("\n已结束，之前提交的反馈已保存。")
    write(json.dumps(store.feedback_summary(), ensure_ascii=False, indent=2))
