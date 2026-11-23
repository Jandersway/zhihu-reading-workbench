"""Personal reading library backed by snapshots and read-only Zhihu CLI commands."""
from collections import OrderedDict
import re
from threading import Lock
from time import monotonic
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from zhihu_agent.kimi import KimiError
from zhihu_agent.recommend import ZhihuGateway

from .demo import DEMO_BODY, TITLE
from .library_research import LibraryCorpus, answer_article
from .model_client import providers
from .model_settings import require_live_model
from .question_suggestions import suggest_questions
from .store import now

router = APIRouter(prefix='/library', tags=['reading-library'])
MAX_BODY = 300_000
LIBRARY_ID = Annotated[str, StringConstraints(pattern=r'^(demo|(?:answer|article)-[0-9]{1,24}|note-[a-f0-9]{32})$')]
SourceURL = Annotated[str, Field(max_length=2000)]
PreviewID = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$')]


class ArticlePreviews:
    """Bounded in-memory snapshots; reading alone never writes to the library."""

    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()

    def put(self, item, account_key):
        preview_id = uuid4().hex
        with self.lock:
            self.entries[preview_id] = (monotonic() + 1800, account_key, item)
            if len(self.entries) > 32:
                self.entries.popitem(last=False)
        return preview_id

    def get(self, preview_id, account_key):
        with self.lock:
            entry = self.entries.get(preview_id)
            if entry is not None:
                expires, owner, item = entry
                if expires <= monotonic():
                    self.entries.pop(preview_id, None)
                elif owner == account_key:
                    return item
        raise HTTPException(410, '预览已过期，请重新加载正文。')


class AddItem(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    source: Literal['zhihu', 'demo', 'note']
    url: SourceURL | None = None
    title: str | None = Field(default=None, max_length=300)
    body: str | None = Field(default=None, max_length=MAX_BODY)
    preview_id: PreviewID | None = None


class ReaderQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=300)
    mode: Literal['demo', 'live'] = 'demo'
    consent: StrictBool = False


class PreviewQuestion(ReaderQuestion):
    preview_id: PreviewID


def zhihu_target(value):
    try:
        url = urlsplit(value or '')
    except ValueError:
        raise HTTPException(422, '无法识别该知乎链接。') from None
    answer = re.fullmatch(r'(/question/[0-9]{1,24})?/answer/([0-9]{1,24})/?', url.path)
    article = re.fullmatch(r'/p/([0-9]{1,24})/?', url.path)
    if url.scheme == 'https' and url.netloc in {'www.zhihu.com', 'zhihu.com'} and answer:
        parent, item_id = answer.groups()
        return 'answer', item_id, f'https://www.zhihu.com{parent or ""}/answer/{item_id}'
    if url.scheme == 'https' and url.netloc == 'zhuanlan.zhihu.com' and article:
        item_id = article[1]
        return 'article', item_id, f'https://zhuanlan.zhihu.com/p/{item_id}'
    raise HTTPException(422, '请粘贴知乎回答或专栏文章的完整 HTTPS 链接。')


def cli_read(args, action, gateway=None):
    try:
        return (gateway or ZhihuGateway()).run(args)
    except RuntimeError as exc:
        if str(exc).startswith('工作台登录已失效'):
            raise HTTPException(401, '工作台登录已失效，请重新登录。') from None
        if str(exc).startswith('知乎登录已失效'):
            raise HTTPException(424, '知乎未接受当前凭据。你可以继续使用阅读库，或重新登录工作台来更新知乎凭据。') from None
        if str(exc).startswith('知乎拒绝'):
            detail = f'知乎拒绝{action}或要求验证，请在知乎检查访问状态后再试。'
        else:
            detail = f'{action}失败，请检查知乎 CLI 登录和网络。'
        raise HTTPException(502, detail) from None


def get_item(store, item_id):
    with store.connect() as db:
        row = db.execute('SELECT * FROM reading_library WHERE id=?', (item_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '这篇内容已从阅读库移除，或尚未加入。')
        return dict(row)


def load_zhihu_item(store, value, gateway):
    kind, content_id, url = zhihu_target(value)
    item_id = f'{kind}-{content_id}'
    with store.connect() as db:
        existing = db.execute('SELECT * FROM reading_library WHERE id=?', (item_id,)).fetchone()
        if existing:
            return dict(existing), True

    result = cli_read(['browse', kind, url], '读取正文', gateway)
    metadata = result.get('metadata') if isinstance(result, dict) else None
    if not isinstance(metadata, dict) or str(metadata.get('id')) != content_id:
        raise HTTPException(502, '知乎返回的正文和链接不匹配。')
    body = result.get('content_md')
    if not isinstance(body, str) or not body.strip():
        raise HTTPException(502, '知乎未返回可阅读的正文。')
    if len(body) > MAX_BODY:
        raise HTTPException(422, '这篇正文超过工作台可读取的长度。可在知乎打开原文。')
    author = metadata.get('author') or ''
    if isinstance(author, dict):
        author = author.get('name') or ''
    return {'id': item_id, 'title': str(metadata.get('title') or '知乎正文')[:300], 'author': str(author)[:120],
            'source': 'zhihu', 'url': url, 'body': body, 'updated_at': now()}, False


def comment_rows(rows, depth=0):
    normalized = []
    for index, row in enumerate(rows[:10] if depth == 0 else rows[:3]):
        if not isinstance(row, dict):
            continue
        children = row.get('child_comments')
        normalized.append({'key': str(index), 'author': str(row.get('author') or '知乎用户')[:120],
                           'content': str(row.get('content') or '')[:3000],
                           'likes': row.get('like_count') if type(row.get('like_count')) is int else 0,
                           'replies': comment_rows(children, depth + 1) if depth < 2 and isinstance(children, list) else []})
    return normalized


@router.get('')
def list_library(request: Request):
    with request.state.store.connect() as db:
        return [dict(row) for row in db.execute('SELECT id,title,author,source,url,updated_at FROM reading_library ORDER BY updated_at DESC,id')]


@router.post('', status_code=201)
def add_item(payload: AddItem, request: Request):
    store = request.state.store
    if payload.preview_id:
        if payload.source != 'zhihu':
            raise HTTPException(422, '仅知乎正文可以从预览保存。')
        document = request.app.state.article_previews.get(payload.preview_id, request.state.account_key)
        item = tuple(document[key] for key in ('id', 'title', 'author', 'source', 'url', 'body'))
    elif payload.source == 'demo':
        item = ('demo', TITLE, '示例作者', 'demo', None, DEMO_BODY)
    elif payload.source == 'note':
        if not payload.title or not payload.body:
            raise HTTPException(422, '手动添加需要标题和正文。')
        url = zhihu_target(payload.url)[2] if payload.url else None
        item = ('note-' + uuid4().hex, payload.title, '', 'note', url, payload.body)
    else:
        document, saved = load_zhihu_item(store, payload.url, request.state.gateway)
        if saved:
            return document
        item = tuple(document[key] for key in ('id', 'title', 'author', 'source', 'url', 'body'))
    with store.connect() as db:
        db.execute('INSERT INTO reading_library VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING', (*item, now()))
        return dict(db.execute('SELECT * FROM reading_library WHERE id=?', (item[0],)).fetchone())


@router.get('/preview')
def preview_item(request: Request, url: SourceURL):
    item, saved = load_zhihu_item(request.state.store, url, request.state.gateway)
    preview_id = None if saved else request.app.state.article_previews.put(item, request.state.account_key)
    return {**item, 'saved': saved, 'preview_id': preview_id}


@router.get('/preview/comments')
def preview_comments(url: SourceURL, request: Request):
    return read_comments_for({'source': 'zhihu', 'url': url}, request.state.gateway)


@router.get('/questions')
def reading_questions(request: Request, item_id: LIBRARY_ID | None = None, preview_id: PreviewID | None = None):
    store = request.state.store
    if item_id and preview_id:
        raise HTTPException(422, '一次只能选择一篇资料或一个预览。')
    if preview_id:
        documents = [request.app.state.article_previews.get(preview_id, request.state.account_key)]
    elif item_id:
        documents = [get_item(store, item_id)]
    else:
        with store.connect() as db:
            documents = db.execute("""SELECT title,substr(body,1,16000) AS body FROM reading_library
                WHERE source != 'demo' ORDER BY updated_at DESC,id LIMIT 3""").fetchall()
    return {'questions': suggest_questions(documents)}


@router.post('/preview/ask')
def ask_preview(payload: PreviewQuestion, request: Request):
    document = request.app.state.article_previews.get(payload.preview_id, request.state.account_key)
    corpus = LibraryCorpus(None, document['id'], snapshot=document)
    return answer_document(corpus, payload, request)


@router.get('/{item_id}')
def read_item(item_id: LIBRARY_ID, request: Request):
    return get_item(request.state.store, item_id)


@router.post('/{item_id}/ask')
def ask_saved(item_id: LIBRARY_ID, payload: ReaderQuestion, request: Request):
    store = request.state.store
    document = get_item(store, item_id)
    corpus = LibraryCorpus(store, item_id, snapshot=document)
    return answer_document(corpus, payload, request)


def answer_document(corpus, payload, request):
    if payload.mode == 'demo':
        result = corpus.attach_citations(corpus.extract(payload.question))
        return {**result, 'generation': {'mode': 'local'}}
    config = require_live_model(request, payload.consent)
    lock = request.state.store.reader_ask_lock
    if not lock.acquire(blocking=False):
        raise HTTPException(409, '这个账号已有一条单篇问答正在运行。')
    try:
        result = answer_article(payload.question, corpus, model_config=config)
    except KimiError:
        raise HTTPException(502, '模型调用失败，请检查可用额度和网络后重试。') from None
    finally:
        lock.release()
    provider = providers()[config.provider]
    return {**corpus.attach_citations(result),
            'generation': {'mode': 'live', 'provider': provider.label, 'model': config.model}}


@router.get('/{item_id}/comments')
def read_comments(item_id: LIBRARY_ID, request: Request):
    return read_comments_for(get_item(request.state.store, item_id), request.state.gateway)


def read_comments_for(item, gateway):
    if item['source'] == 'note':
        raise HTTPException(404, '手动添加的资料没有知乎评论。')
    if item['source'] == 'demo':
        rows = [{'author': '示例读者', 'content': '引用确实存在，还要核对它能不能支持结论。', 'like_count': 3,
                 'child_comments': [{'author': '示例作者', 'content': '对，这就是把程序校验和语义评审拆开的原因。'}]}]
    else:
        _, _, url = zhihu_target(item['url'])
        rows = cli_read(['browse', 'comments', url, '--limit', '10', '--max', '10'], '读取评论', gateway)
    if not isinstance(rows, list):
        raise HTTPException(502, '知乎返回的评论格式异常。')
    return {'comments': comment_rows(rows), 'source': item['source']}


@router.delete('/{item_id}', status_code=204)
def remove_item(item_id: LIBRARY_ID, request: Request):
    with request.state.store.connect() as db:
        db.execute('DELETE FROM reading_library WHERE id=?', (item_id,))
    return Response(status_code=204)
