"""Local HTTP adapter for the existing Zhihu Agent and ranking evaluation."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints

from zhihu_agent.config import Settings
from zhihu_agent.corpus import Corpus
from zhihu_agent.evidence_graph import build_evidence_graph
from zhihu_agent.ranking_eval import evaluate_ranking
from zhihu_agent.recommend import Preferences, ReadingStore, RecommendationSession, ZhihuGateway
from zhihu_agent.runtime_skills import load_skill
from zhihu_agent.workflow import WorkflowLimits, recommend_workflow
from zhihu_agent.kimi import KimiError

from .demo import research_demo, run_demo
from .agent_rl import experiment
from .accounts import AccountGateway, AccountSessions, SESSION_COOKIE, router as auth_router
from .discover import router as discover_router
from .discovery_evaluation import router as discovery_evaluation_router
from .lab import Lab
from .library import ArticlePreviews, LIBRARY_ID, get_item, router as library_router
from .library_research import LibraryCorpus, research_live
from .model_client import ModelClient
from .model_settings import AccountModelKeys, require_live_model, router as model_router
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger('uvicorn.error')


class TaskInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    nonce: UUID
    focus: str = Field(min_length=1, max_length=300)
    mode: Literal['demo', 'live'] = 'demo'
    kind: Literal['recommend', 'research'] = 'recommend'
    research_scope: Literal['original', 'library'] = 'original'
    library_item_id: LIBRARY_ID | None = None
    consent: StrictBool = False


class Grade(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: Annotated[str, StringConstraints(pattern=r'^(article|answer):[0-9]{1,24}$')]
    grade: Annotated[StrictInt, Field(ge=0, le=2)] | None


class Ratings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[Grade] = Field(min_length=1, max_length=20)


def run_live(focus, progress, *, model_config, account_store=None, gateway=None):
    settings = Settings.from_env()
    client = ModelClient(model_config, max_tokens=4096)
    if account_store is not None:
        with account_store.connect() as db:
            rows = db.execute("""SELECT id,title,author,url,body FROM reading_library
                               WHERE source='zhihu' ORDER BY updated_at DESC LIMIT 100""").fetchall()
        if not rows:
            raise HTTPException(422, '请先保存一篇知乎文章，供实时推荐查找偏好证据。')
        with TemporaryDirectory(dir=account_store.path.parent) as folder:
            articles = {}
            for row in rows:
                kind, aid = row['id'].split('-', 1)
                key = aid if kind == 'answer' else row['id']
                path = Path(folder) / (row['id'] + '.md')
                path.write_text(row['body'], encoding='utf-8')
                articles[key] = SimpleNamespace(collection='favorites', file=path,
                                                title=row['title'], author=row['author'], source=row['url'])
            corpus = SimpleNamespace(articles=articles)
            session = RecommendationSession(corpus, Preferences(corpus), ReadingStore(account_store.path.parent / 'reading'),
                                            gateway, count=1, feed_limit=12, max_reads=3)
            return run_workflow(session, client, focus, progress)
    corpus = Corpus(settings)
    session = RecommendationSession(corpus, Preferences(corpus), ReadingStore(settings.data_dir.parent / 'reading'),
                                    ZhihuGateway(), count=1, feed_limit=12, max_reads=3)
    return run_workflow(session, client, focus, progress)


def run_workflow(session, client, focus, progress):
    return recommend_workflow(session, client, focus, limits=WorkflowLimits(model_calls=8, total_tokens=65000),
                              on_progress=progress, skill=load_skill('personalized-reading', mode='recommend'))


def project_report(report):
    fields = ('run_id', 'status', 'completed', 'reason', 'candidate_ranking', 'home_candidate_ids', 'cost_breakdown',
              'elapsed_ms', 'usage_totals', 'usage_complete', 'skill')
    result = {key: report[key] for key in fields if key in report}
    result['candidates'] = [{key: row.get(key) for key in ('candidate_id', 'title', 'excerpt', 'url')}
                            for row in report['candidates']]
    result['recommendations'] = [{key: row.get(key) for key in ('candidate_id', 'title', 'url', 'why', 'value',
                                  'drawback', 'preference_reason', 'evidence_quote', 'preference_quote')}
                                 for row in report['recommendations']]
    result['reviews'] = report['workflow']['reviews']
    result['graph'] = build_evidence_graph(report) if result['recommendations'] else None
    return result


def execute_task(store, task_id, task, gateway=None, *, model_config=None):
    def progress(text):
        if text.startswith('工作流 → '):
            stage, _, detail = text.removeprefix('工作流 → ').partition('：')
            store.event(task_id, stage, detail)

    try:
        if not store.start(task_id):
            return
        if task.kind == 'research':
            store.event(task_id, 'research', '搜索本地资料、读取原文并检查引用编号。')
            if task.research_scope == 'library':
                if task.library_item_id:
                    get_item(store, task.library_item_id)
                corpus = LibraryCorpus(store, task.library_item_id)
                result = (corpus.extract(task.focus) if task.mode == 'demo'
                          else research_live(task.focus, corpus, model_config=model_config).as_dict())
                result = corpus.attach_citations(result)
            else:
                result = (research_demo(task.focus) if task.mode == 'demo'
                          else research_live(task.focus, model_config=model_config)).as_dict()
            store.finish(task_id, result, error=None if result['completed'] else result['answer'])
        else:
            if task.mode == 'demo':
                report = run_demo(task.focus, progress)
            elif gateway is not None:
                report = run_live(task.focus, progress, model_config=model_config, account_store=store, gateway=gateway)
            else:
                report = run_live(task.focus, progress, model_config=model_config)
            result = project_report(report)
            store.finish(task_id, result, error=None if result['completed'] else result['reason'])
    except HTTPException as exc:
        store.finish(task_id, error=str(exc.detail))
    except KimiError:
        store.finish(task_id, error='模型接口调用失败。请检查提供商、模型 ID、API Key 和可用额度。')
    except Exception as exc:
        LOGGER.error('task=%s failed type=%s', task_id, type(exc).__name__)
        store.finish(task_id, error='工作流或报告处理失败。可在后端日志按任务编号查询错误类型。')


def create_app(*, db_path=None, cohort_file=None, run_dir=None, require_login=True):
    @asynccontextmanager
    async def lifespan(app):
        settings = Settings.from_env()
        legacy_db = db_path or Path(os.environ.get('ZHIHU_WEB_DB', ROOT / '.runtime' / 'workspace.sqlite3'))
        app.state.accounts = AccountSessions(legacy_db.parent / 'accounts')
        app.state.model_keys = AccountModelKeys(app.state.accounts.root)
        if not require_login:
            store = Store(legacy_db)
            store.init()
            app.state.store = store
        app.state.article_previews = ArticlePreviews()
        archive = cohort_file or os.environ.get('ZHIHU_WEB_COHORT')
        app.state.lab = None
        if archive:
            try:
                app.state.lab = Lab(Path(archive), run_dir or settings.data_dir.parent / 'reading' / 'runs')
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                LOGGER.warning('Ranking lab unavailable: %s', type(exc).__name__)
        app.state.live_allowed = os.environ.get('ZHIHU_WEB_ENABLE_LIVE', '1') == '1'
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='reading') as executor:
            app.state.executor = executor
            yield

    app = FastAPI(title='Zhihu Agent Workbench', version='1.0.0', lifespan=lifespan)
    app.include_router(library_router)
    app.include_router(discover_router)
    app.include_router(discovery_evaluation_router)
    app.include_router(auth_router)
    app.include_router(model_router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        if request.url.path == '/auth/login':
            return JSONResponse({'detail': '请输入有效长度的知乎 Cookie 请求头。'}, status_code=422,
                                headers={'Cache-Control': 'no-store'})
        if request.url.path == '/settings/model':
            return JSONResponse({'detail': '请检查模型设置的格式或长度。'}, status_code=422,
                                headers={'Cache-Control': 'no-store'})
        return await request_validation_exception_handler(request, exc)

    @app.middleware('http')
    async def local_requests(request: Request, call_next):
        if request.url.hostname not in {'127.0.0.1', 'localhost', 'testserver'}:
            return JSONResponse({'detail': '仅支持本机访问。'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '仅支持从工作台发起请求。'}, status_code=403)
        if request.method in {'POST', 'PUT', 'DELETE'} and request.headers.get('x-zhihu-workbench') != '1':
            return JSONResponse({'detail': '缺少工作台请求头。'}, status_code=403)
        if request.url.path not in {'/health', '/auth/session', '/auth/login', '/auth/logout'}:
            token = request.cookies.get(SESSION_COOKIE)
            session = app.state.accounts.get(token)
            if session:
                request.state.store = app.state.accounts.store(session)
                request.state.gateway = AccountGateway(app.state.accounts, token)
                request.state.account_key = session.key
            elif not require_login:
                request.state.store = app.state.store
                request.state.gateway = None
                request.state.account_key = None
            else:
                return JSONResponse({'detail': '请先登录知乎工作台。'}, status_code=401,
                                    headers={'Cache-Control': 'no-store'})
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    def lab_service():
        if app.state.lab is None:
            raise HTTPException(503, '排序评测档案缺失或未通过校验。其他阅读功能可继续使用。')
        return app.state.lab

    @app.get('/tasks')
    def recent_tasks(request: Request):
        return request.state.store.recent()

    @app.post('/tasks', status_code=202)
    def new_task(task: TaskInput, request: Request):
        model_config = require_live_model(request, task.consent) if task.mode == 'live' else None
        store = request.state.store
        if request.state.gateway and task.kind == 'research' and task.mode == 'live' and task.research_scope != 'library':
            raise HTTPException(422, '实时问答只读取当前账号的阅读库。')
        if task.research_scope == 'library' and task.kind != 'research':
            raise HTTPException(422, '阅读库范围仅适用于资料问答。')
        if task.library_item_id:
            if task.research_scope != 'library':
                raise HTTPException(422, '指定文章需要选择阅读库范围。')
            get_item(store, task.library_item_id)
        try:
            task_id, created = store.create(str(task.nonce), task.mode, task.focus, task.kind,
                                            task.research_scope, task.library_item_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        if created:
            try:
                app.state.executor.submit(execute_task, store, task_id, task, request.state.gateway, model_config=model_config)
            except RuntimeError:
                store.finish(task_id, error='后端正在关闭，请重新发起。')
        return {'id': task_id}

    @app.get('/tasks/{task_id}')
    def task_detail(task_id: str, request: Request):
        try:
            return request.state.store.task(task_id)
        except KeyError:
            raise HTTPException(404, '任务不存在。') from None

    @app.get('/lab/cases')
    def lab_cases(request: Request):
        stored = request.state.store.annotations()
        return [{'case_id': cid, 'focus': case['focus'], 'count': len(case['candidates']),
                 'labeled': sum(item['grade'] is not None for item in stored.get(cid, {}).get('items', []))}
                for cid, case in lab_service().cases.items()]

    @app.get('/lab/cases/{case_id}')
    def lab_form(case_id: str, request: Request):
        try:
            return lab_service().form(case_id, request.state.store.annotations().get(case_id))
        except KeyError:
            raise HTTPException(404, '评测批次不存在。') from None

    @app.get('/lab/cases/{case_id}/comparison')
    def lab_compare(case_id: str):
        try:
            return lab_service().compare(case_id)
        except KeyError:
            raise HTTPException(404, '评测批次不存在。') from None

    @app.put('/lab/cases/{case_id}/ratings')
    def lab_ratings(case_id: str, ratings: Ratings, request: Request):
        case = lab_service().cases.get(case_id)
        if case is None:
            raise HTTPException(404, '评测批次不存在。')
        annotation = {**ratings.model_dump(), 'case_id': case_id, 'label_source': 'human', 'annotator': 'local-web'}
        if len(annotation['items']) != len(case['candidates']):
            raise HTTPException(422, '请保留批次中的全部候选，未知项可选「无法判断」。')
        try:
            checked = evaluate_ranking(case, annotation, 3)
        except ValueError:
            raise HTTPException(422, '评分候选与固定批次不匹配。') from None
        request.state.store.save_annotation(annotation)
        return {'labeled': checked['labeled_count']}

    @app.get('/lab/metrics')
    def lab_metrics(request: Request, source: Literal['human', 'ai_proxy'] = 'human'):
        return lab_service().metrics(request.state.store.annotations(), source)

    @app.get('/lab/agent-rl')
    def agent_rl():
        return experiment()

    return app


app = create_app()
