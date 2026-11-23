"""Account identity and server-side browser sessions."""
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import logging
import os
import re
from secrets import token_urlsafe
from threading import Lock
from time import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .store import Store
from zhihu_agent.recommend import ZhihuGateway

from .toolkit import AccountTransport
from .windows_storage import WindowsProtectedFile

SESSION_COOKIE = 'zhihu_workbench_session'
SESSION_SECONDS = 30 * 24 * 3600
RENEW_AFTER = 24 * 3600
LOGGER = logging.getLogger(__name__)
COOKIE_PREFIX = re.compile(r'^cookie[ \t]*(?::|\r?\n)', re.IGNORECASE | re.ASCII)
COOKIE_NAME = re.compile(r"[!#$%&'*+.^_`|~\w-]+", re.ASCII)
router = APIRouter(prefix='/auth', tags=['session'])


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    cookie: str = Field(min_length=1, max_length=16384)


def parse_cookie(pasted):
    value = COOKIE_PREFIX.sub('', pasted.strip(' \t\r\n\ufeff'), count=1)
    fields = []
    has_login = False
    for part in value.split(';'):
        part = part.strip(' \t\r\n')
        if not part:
            continue
        name, separator, content = part.partition('=')
        name = name.strip(' \t')
        content = content.strip(' \t')
        if not separator or not COOKIE_NAME.fullmatch(name) or any(not 32 <= ord(char) <= 126 for char in content):
            raise HTTPException(422, '粘贴的内容格式不对。请只复制「请求标头」中 Cookie 那一项的值。')
        fields.append(f'{name}={content}')
        if name == 'z_c0' and content.strip('" ').strip():
            has_login = True
    if not has_login:
        raise HTTPException(422, '这段内容没有有效的 z_c0。请确认知乎已登录，刷新后从 www.zhihu.com 的请求重新复制 Cookie。')
    return '; '.join(fields)


def verify_account(cookie):
    try:
        status = AccountTransport(lambda: cookie).run(['auth', 'status'])
    except RuntimeError:
        raise HTTPException(502, '暂时无法连接知乎验证账号，请稍后再试。') from None
    if not isinstance(status, dict):
        raise HTTPException(502, '知乎未返回有效的账号验证结果。')
    http_status = status.get('auth_http_status')
    if http_status == 403:
        raise HTTPException(403, '知乎拒绝验证，请在知乎官网完成验证后重新复制 Cookie。')
    if http_status is None or http_status in {429, 500, 502, 503, 504}:
        raise HTTPException(502, '暂时无法连接知乎验证账号，请稍后再试。')
    user_id = status.get('user_id')
    if status.get('authenticated') is not True or not isinstance(user_id, (str, int)) or not user_id:
        raise HTTPException(401, '知乎登录凭据无效或已过期，请重新复制。')
    key = sha256(str(user_id).encode('utf-8')).hexdigest()
    return key, str(status.get('username') or '知乎用户')[:80]


@dataclass(frozen=True)
class BrowserSession:
    key: str
    name: str
    cookie: str = field(repr=False)
    expires: float

    def public(self):
        return {'name': self.name, 'account': self.key}


class AccountGateway(ZhihuGateway):
    def __init__(self, accounts, token):
        def active_cookie():
            session = accounts.get(token)
            return session.cookie if session else None
        self.transport = AccountTransport(active_cookie)

    def run(self, args):
        return self.transport.run(args)


class AccountSessions:
    def __init__(self, root):
        self.root = root
        self.stores = {}
        self.lock = Lock()
        self.session_file = WindowsProtectedFile(root / 'sessions.dpapi') if os.name == 'nt' else None
        self.sessions = self._restore()

    def _restore(self):
        if self.session_file is None:
            return {}
        try:
            now = time()
            restored = {key: BrowserSession(**row) for key, row in self.session_file.read().items()}
            return {key: session for key, session in restored.items() if session.expires > now}
        except (OSError, ValueError, TypeError, AttributeError):
            LOGGER.warning('Could not restore saved workbench sessions')
            return {}

    def _save(self, sessions):
        if self.session_file is not None:
            try:
                self.session_file.write({key: asdict(session) for key, session in sessions.items()})
            except OSError:
                raise HTTPException(503, '无法保存本机登录状态，请检查工作台的数据目录后重试。') from None
        self.sessions = sessions

    @staticmethod
    def _token_key(token):
        return sha256(token.encode('utf-8')).hexdigest() if token else None

    def _active(self, key, now):
        session = self.sessions.get(key)
        return session if session and session.expires > now else None

    def issue(self, key, name, cookie, previous):
        token = token_urlsafe(32)
        previous_key = self._token_key(previous)
        with self.lock:
            now = time()
            sessions = {k: v for k, v in self.sessions.items() if v.expires > now and k != previous_key}
            if len(sessions) >= 128:
                raise HTTPException(503, '登录会话已满，请稍后再试。')
            session = BrowserSession(key, name, cookie, now + SESSION_SECONDS)
            sessions[self._token_key(token)] = session
            self._save(sessions)
        return token, session

    def get(self, token):
        with self.lock:
            return self._active(self._token_key(token), time())

    def renew(self, token):
        key = self._token_key(token)
        with self.lock:
            now = time()
            session = self._active(key, now)
            if session is None or session.expires - now > SESSION_SECONDS - RENEW_AFTER:
                return session, False
            updated = replace(session, expires=now + SESSION_SECONDS)
            self._save({**self.sessions, key: updated})
            return updated, True

    def end(self, token):
        key = self._token_key(token)
        with self.lock:
            if key in self.sessions:
                self._save({k: v for k, v in self.sessions.items() if k != key})

    def store(self, session):
        with self.lock:
            if session.key not in self.stores:
                store = Store(self.root / session.key / 'workspace.sqlite3')
                store.init()
                self.stores[session.key] = store
            return self.stores[session.key]


def set_session_cookie(request, response, token):
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite='strict',
                        secure=request.url.scheme == 'https')


@router.get('/session')
def current_session(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    session, renewed = request.app.state.accounts.renew(token)
    if renewed:
        set_session_cookie(request, response, token)
    return {'user': session.public() if session else None}


@router.post('/login')
def login(payload: Login, request: Request, response: Response):
    cookie = parse_cookie(payload.cookie)
    key, name = verify_account(cookie)
    token, session = request.app.state.accounts.issue(key, name, cookie, request.cookies.get(SESSION_COOKIE))
    set_session_cookie(request, response, token)
    return {'user': session.public()}


@router.post('/logout', status_code=204)
def logout(request: Request):
    request.app.state.accounts.end(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite='strict', secure=request.url.scheme == 'https')
    return response
