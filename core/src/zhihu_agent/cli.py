from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .agent import ZhihuAgent
from .config import Settings
from .corpus import Corpus
from .evaluation import load_cases, run_cases
from .kimi import KimiClient, KimiError
from .ingest import ingest_answer
from .recommend import Preferences, ReadingStore, RecommendationSession, ZhihuGateway, recommend, render
from .candidate_list import load_snapshot, make_candidate_list, render_candidate_list
from .workflow import WorkflowLimits, recommend_workflow
from .reading_review import rate, review
from .runtime_skills import list_skills, load_skill
from .reading_eval import annotation_template, evaluate_reading
from .evidence_graph import build_evidence_graph
from .ranking import rank_candidates
from .ranking_eval import evaluate_ranking, evaluation_case, rating_template


PROJECT_DIR = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description="Source-grounded Zhihu Markdown agent")
    sub = parser.add_subparsers(dest="command", required=True)

    skills_parser = sub.add_parser("skills", help="List or inspect local runtime skills; no key, corpus or network needed")
    skills_parser.add_argument("name", nargs="?", help="Show one skill including its instructions")
    skills_parser.add_argument("--mode", choices=["ask", "recommend"], help="Filter compatible skills")

    search_parser = sub.add_parser("search", help="Search local indexes without using Kimi")
    search_parser.add_argument("query")
    search_parser.add_argument("--author", default="")
    search_parser.add_argument("--limit", type=int, default=5)

    read_parser = sub.add_parser("read", help="Read a locally indexed answer")
    read_parser.add_argument("answer_id")
    read_parser.add_argument("--start", type=int, default=0)

    ingest_parser = sub.add_parser("ingest", help="Save one explicitly specified answer via the existing Zhihu CLI")
    ingest_parser.add_argument("url")

    ask_parser = sub.add_parser("ask", help="Let K3 choose search/read tools and answer")
    ask_parser.add_argument("question")
    ask_parser.add_argument("--trace", action="store_true")
    ask_parser.add_argument("--skill", help="Explicit local runtime skill, e.g. source-grounded-research")

    sub.add_parser("preferences", help="Inspect provisional preference evidence from favorites only; no Kimi call")
    feed_parser = sub.add_parser("feed", help="Preview live homepage candidates with similar favorites; no Kimi call")
    feed_parser.add_argument("--limit", type=int, default=15, choices=range(1, 21))
    feed_parser.add_argument("--focus", default="", help="Show a goal-based shortlist with original ranks")
    candidates_parser = sub.add_parser("candidates", help="Ask Kimi to list all saved feed candidates for user selection")
    candidates_parser.add_argument("--snapshot", type=Path, help="Defaults to the last saved homepage snapshot")
    recommend_parser = sub.add_parser("recommend", help="Let Kimi select, read and recommend homepage content")
    recommend_parser.add_argument("--count", type=int, default=3, choices=range(1, 6))
    recommend_parser.add_argument("--feed-limit", type=int, default=15, choices=range(1, 21))
    recommend_parser.add_argument("--max-reads", type=int, default=6, choices=range(1, 11))
    recommend_parser.add_argument("--focus", default="", help="Optional interest for this run")
    recommend_parser.add_argument("--ranking", choices=["focus", "homepage"], default="focus",
                                  help="Workflow plan order when a reading focus is supplied")
    recommend_parser.add_argument("--trace", action="store_true")
    recommend_parser.add_argument("--skill", help="Explicit workflow skill, e.g. personalized-reading; incompatible with --mode react")
    recommend_parser.add_argument("--mode", choices=["workflow", "react"], default="workflow",
                                  help="workflow: plan/execute/validate/reflect; react: original baseline")
    recommend_parser.add_argument("--max-model-calls", type=int, choices=range(1, 21), default=10)
    recommend_parser.add_argument("--max-revisions", type=int, choices=range(0, 3), default=1)
    recommend_parser.add_argument("--context-chars", type=int, default=60000)
    recommend_parser.add_argument('--max-total-tokens', type=int, default=0,
                                  help='Workflow only: stop before next model call when observed usage reaches this threshold; 0 disables, one call may exceed it')
    reading_eval_parser = sub.add_parser('reading-eval', help='Offline human-label evaluation; without annotations prints a blank labeling template')
    reading_eval_parser.add_argument('--run', default='latest')
    reading_eval_parser.add_argument('--annotations', type=Path)
    for name, help_text in (
        ('rank-preview', 'Replay a goal-based candidate ranking from a saved run'),
        ('rank-eval', 'Create a shuffled labeling form or compare rankings on a saved run'),
    ):
        rank_parser = sub.add_parser(name, help=help_text)
        rank_parser.add_argument('--run', default='latest')
        rank_parser.add_argument('--focus', help='Override the saved reading goal for replay')
        if name == 'rank-eval':
            rank_parser.add_argument('--annotations', type=Path)
            rank_parser.add_argument('--k', type=int, default=3)
    graph_parser = sub.add_parser('evidence-graph', help='Export a local typed evidence graph from a version 3 report; no API call')
    graph_parser.add_argument('--run', default='latest')
    graph_parser.add_argument('--candidate-id')
    feedback_parser = sub.add_parser("feedback", help="Record feedback locally; no Zhihu account mutation")
    feedback_parser.add_argument("candidate_id")
    feedback_parser.add_argument("verdict", choices=["like", "dislike", "seen", "too_shallow", "clear"])
    feedback_parser.add_argument("--note", default="")
    feedback_parser.add_argument("--scope", choices=["article", "topic", "depth"], default="article")
    feedback_parser.add_argument("--topic", default="")
    for command in ("results", "review", "rate"):
        item_parser = sub.add_parser(command, help={"results": "Show saved recommendations without an API call",
            "review": "Interactively rate recommendations locally", "rate": "Rate by number in a saved report"}[command])
        item_parser.add_argument("--run", default="latest", help="Report run ID; latest means latest nonempty report")
        if command == "rate":
            item_parser.add_argument("number", type=int)
            item_parser.add_argument("verdict", choices=["like", "dislike", "seen", "too_shallow", "clear"])
            item_parser.add_argument("--note", default="")
            item_parser.add_argument("--scope", choices=["article", "topic", "depth"], default="article")
            item_parser.add_argument("--topic", default="")
    sub.add_parser("feedback-summary", help="Count user ratings; unrated results are not dislikes")

    eval_parser = sub.add_parser("eval", help="Run a subset of the 20 evaluation questions")
    eval_parser.add_argument("--case", type=int, action="append", help="1..20; repeat to run multiple")
    eval_parser.add_argument("--all", action="store_true", help="Calls Kimi for all 20 cases")
    eval_parser.add_argument("--skill", help="Optional ask-mode skill; identity is included in evaluation records")
    eval_parser.add_argument("--output", type=Path, default=PROJECT_DIR / "reports" / "eval.jsonl")

    args = parser.parse_args()
    try:
        execute(args, parser)
    except (ValueError, KeyError, FileNotFoundError, FileExistsError, KimiError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(1) from None


def execute(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.command == 'recommend' and args.max_total_tokens:
        if args.mode != 'workflow':
            raise ValueError('--max-total-tokens requires --mode workflow')
        WorkflowLimits(total_tokens=args.max_total_tokens)  # Fail before loading corpus or credentials.
    if args.command == "skills":
        if args.name:
            skill = load_skill(args.name, mode=args.mode)
            print(json.dumps(dict(skill.identity(), description=skill.description), ensure_ascii=False, indent=2))
            print("\n" + skill.instructions)
        else:
            print(json.dumps([dict(s.identity(), description=s.description) for s in list_skills(mode=args.mode)],
                             ensure_ascii=False, indent=2))
        return
    skill = None
    if getattr(args, "skill", None):
        if args.command == "recommend" and args.mode != "workflow":
            raise ValueError("Runtime skills require recommend --mode workflow; react remains the original baseline")
        skill = load_skill(args.skill, mode="recommend" if args.command == "recommend" else "ask")
        print(f"使用技能：{skill.name} · sha256={skill.sha256}", file=sys.stderr)
    settings = Settings.from_env()
    store = ReadingStore(settings.data_dir.parent / "reading")
    if args.command in {'rank-preview', 'rank-eval'}:
        case = evaluation_case(store.report_by_id(args.run), args.focus)
        if args.command == 'rank-preview':
            result = case
        elif args.annotations:
            labels = json.loads(args.annotations.read_text(encoding='utf-8-sig'))
            result = evaluate_ranking(case, labels, args.k)
        else:
            result = rating_template(case)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == 'evidence-graph':
        print(json.dumps(build_evidence_graph(store.report_by_id(args.run), args.candidate_id), ensure_ascii=False, indent=2))
        return
    if args.command == 'reading-eval':
        report = store.report_by_id(args.run)
        result = (evaluate_reading(report, json.loads(args.annotations.read_text(encoding='utf-8-sig')))
                  if args.annotations else annotation_template(report))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "results":
        print(render(store.report_by_id(args.run)))
        return
    if args.command == "review":
        review(store, args.run)
        return
    if args.command == "rate":
        print(json.dumps(rate(store, args.number, args.verdict, run_id=args.run, note=args.note,
                             scope=args.scope, topic=args.topic), ensure_ascii=False, indent=2))
        return
    if args.command == "feedback-summary":
        print(json.dumps(store.feedback_summary(), ensure_ascii=False, indent=2))
        return
    if args.command == "candidates":
        snapshot_path = args.snapshot or store.root / "previews" / "latest-candidates.json"
        snapshot = load_snapshot(snapshot_path)
        client = KimiClient(settings.kimi_api_key, settings.kimi_base_url, max_tokens=4096)
        print("正在请 Kimi 整理全部候选；使用已保存列表，不刷新首页……", flush=True)
        report = make_candidate_list(snapshot, client)
        output = store.root / "previews" / "kimi-candidates.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(render_candidate_list(report))
        print(f"\n清单保存于：{output}")
        return
    if args.command == "feedback":
        store.feedback(args.candidate_id, args.verdict, args.note, scope=args.scope, topic=args.topic)
        print("反馈已保存在本地，后续推荐会参考。没有执行知乎点赞、收藏或关注。")
        return
    if args.command == "ingest":
        output = ingest_answer(args.url, settings)
        print(f"Saved: {output}")
        print("Run a new search command (or restart the API) to include the new answer.")
        return
    corpus = Corpus(settings)

    if args.command in {"preferences", "feed", "recommend"}:
        preferences = Preferences(corpus)
        if args.command == "preferences":
            print(json.dumps(preferences.profile(), ensure_ascii=False, indent=2))
        else:
            session = RecommendationSession(corpus, preferences, store, ZhihuGateway(),
                count=getattr(args, "count", 3), feed_limit=getattr(args, "feed_limit", getattr(args, "limit", 15)),
                max_reads=getattr(args, "max_reads", 6))
            if args.command == "feed":
                feed = session.get_feed()
                if args.focus.strip():
                    feed["focus_ranking"] = rank_candidates(feed["candidates"], args.focus)
                print(json.dumps(feed, ensure_ascii=False, indent=2))
            else:
                client = KimiClient(settings.kimi_api_key, settings.kimi_base_url, max_tokens=4096)
                print("正在分析收藏线索、读取首页候选并筛选正文，可能需要几分钟……", flush=True)
                progress = lambda text: print(text, flush=True)
                if args.mode == "workflow":
                    limits = WorkflowLimits(model_calls=args.max_model_calls, revisions=args.max_revisions,
                                            context_chars=args.context_chars, total_tokens=args.max_total_tokens)
                    result = recommend_workflow(session, client, args.focus, limits=limits, on_progress=progress,
                                                skill=skill, ranking=args.ranking)
                else:
                    result = recommend(session, client, args.focus, on_progress=progress)
                print(render(result))
                if args.trace:
                    print(json.dumps(result["trace"], ensure_ascii=False, indent=2))
                    if result.get("workflow"):
                        print("状态流转：")
                        print(json.dumps(result["workflow"]["transitions"], ensure_ascii=False, indent=2))
                if not result["completed"]:
                    raise SystemExit(1)
        return

    if args.command == "search":
        print(json.dumps(corpus.search(args.query, author=args.author, limit=args.limit), ensure_ascii=False, indent=2))
    elif args.command == "read":
        print(json.dumps(corpus.read(args.answer_id, start=args.start), ensure_ascii=False, indent=2))
    else:
        agent = ZhihuAgent(corpus, KimiClient(settings.kimi_api_key, settings.kimi_base_url), skill=skill)
        if args.command == "ask":
            result = agent.ask(args.question)
            print(result.answer)
            print("\n来源（本轮已读取）：")
            for source in result.sources:
                print(f"[{source['citation_id']}] {source['author']} · {source['title']} · {source['url']}")
            if result.citation_warning:
                print("\n提示：回答中的引用标记需要人工核对。")
            if args.trace:
                print("\n执行记录：")
                print(json.dumps(result.trace, ensure_ascii=False, indent=2))
        else:
            cases = load_cases(PROJECT_DIR / "eval" / "20_questions.md")
            if not args.all and not args.case:
                parser.error("eval requires --case N or --all")
            numbers = set(args.case or range(1, 21))
            if not numbers.issubset(set(range(1, 21))):
                parser.error("case numbers must be 1..20")
            selected = [case for case in cases if case.number in numbers]
            metrics = run_cases(agent, selected, args.output)
            print(json.dumps({"metrics": metrics, "report": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
