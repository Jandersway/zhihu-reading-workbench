from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path

from zhihu_agent.agent import ZhihuAgent
from zhihu_agent.config import Settings
from zhihu_agent.corpus import Corpus, tokenize
from zhihu_agent.evaluation import load_cases
from zhihu_agent.ingest import save_browse_result


class ScriptedClient:
    def __init__(self, messages: list[dict]):
        self.messages = iter(messages)

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        return {"choices": [{"message": next(self.messages)}]}


def call(call_id: str, name: str, arguments: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}],
    }


class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        scratch = tempfile.TemporaryDirectory()
        cls.addClassCleanup(scratch.cleanup)
        root = Path(scratch.name)
        folder = root / "collections" / "example"
        folder.mkdir(parents=True)
        body = "给代码 Agent 提供 IDE 测试、文档和日志，能帮助它核对生成结果。"
        url = "https://www.zhihu.com/answer/101"
        article = save_browse_result(url, {
            "metadata": {"id": "101", "title": "代码 Agent 的验证反馈", "author": "示例作者"},
            "content_md": body,
        }, folder)
        counts = Counter(tokenize(body))
        (folder / "rag_index.json").write_text(json.dumps({"chunks": [{
            "source": url, "file": str(article), "title": "代码 Agent 的验证反馈",
            "text": body, "term_counts": dict(counts), "length": sum(counts.values()),
        }]}), encoding="utf-8")
        cls.settings = replace(Settings.from_env(), project_root=root, source_root=str(root), data_dir=root / "answers")
        cls.corpus = Corpus(cls.settings)

    def test_existing_index_search_and_read(self) -> None:
        hits = self.corpus.search("Agent IDE 测试 文档 日志 反馈", author="示例作者", limit=3)
        self.assertEqual(hits[0]["answer_id"], "101")
        self.assertEqual(hits[0]["author"], "示例作者")
        article = self.corpus.read(hits[0]["answer_id"])
        self.assertIn("IDE", article["text"])
        self.assertEqual(article["source"], hits[0]["source"])

    def test_model_chosen_search_then_read_then_cited_answer(self) -> None:
        client = ScriptedClient(
            [
                call("c1", "search_answers", '{"query":"Agent IDE 测试 文档 日志 反馈","author":"示例作者"}'),
                call("c2", "read_answer", '{"answer_id":"101"}'),
                {"role": "assistant", "content": "作者主张给 Agent 提供测试、文档和日志等反馈能力。[S1]"},
            ]
        )
        result = ZhihuAgent(self.corpus, client).ask("怎样让代码 Agent 获得有效反馈？")
        self.assertTrue(result.completed)
        self.assertEqual([step["tool"] for step in result.trace], ["search_answers", "read_answer"])
        self.assertEqual(result.sources[0]["answer_id"], "101")
        self.assertFalse(result.citation_warning)

    def test_no_read_forces_abstention(self) -> None:
        client = ScriptedClient([{"role": "assistant", "content": "我猜是42。"}])
        result = ZhihuAgent(self.corpus, client).ask("未知事实是什么？")
        self.assertIn("资料不足", result.answer)
        self.assertEqual(result.sources, [])

    def test_read_only_accepts_indexed_numeric_id(self) -> None:
        with self.assertRaises(ValueError):
            self.corpus.read("../../secrets")
        with self.assertRaises(KeyError):
            self.corpus.read("999999999999999999999999")

    def test_20_eval_cases_parse(self) -> None:
        rows = ["- [A] [示例资料甲](https://www.zhihu.com/answer/101)",
                "- [B] [示例资料乙](https://www.zhihu.com/answer/102)"]
        for index in range(1, 21):
            kind = "资料不足" if index > 17 else "检索"
            refs = "[A] [B]" if index == 14 else "[A]" if index <= 17 else ""
            rows.append(f"| {index:02d} | {kind} | 合成问题 {index} | 预期结果 | {refs} |")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "eval.md"
            path.write_text("\n".join(rows), encoding="utf-8")
            cases = load_cases(path)
        self.assertEqual(len(cases), 20)
        self.assertEqual(sum(case.expect_abstain for case in cases), 3)
        self.assertEqual(len(cases[13].gold_urls), 2)

    def test_explicit_answer_ingest_is_searchable_without_changing_original_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            url = "https://www.zhihu.com/answer/987654321012345678"
            output = save_browse_result(
                url,
                {
                    "metadata": {"id": "987654321012345678", "title": "独特测试问题", "author": "测试作者"},
                    "content_md": "特别的测试证据说明可以增量检索。",
                },
                data_dir,
            )
            self.assertTrue(output.is_file())
            corpus = Corpus(replace(self.settings, data_dir=data_dir))
            hits = corpus.search("特别的测试证据", limit=3)
            self.assertEqual(hits[0]["answer_id"], "987654321012345678")
            self.assertEqual(hits[0]["author"], "测试作者")
            with self.assertRaises(FileExistsError):
                save_browse_result(
                    url,
                    {"metadata": {"id": "987654321012345678"}, "content_md": "重复"},
                    data_dir,
                )

    def test_ingest_rejects_answer_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_browse_result(
                    "https://www.zhihu.com/answer/123",
                    {"metadata": {"id": "456"}, "content_md": "错误的回答"},
                    Path(directory),
                )


if __name__ == "__main__":
    unittest.main()
