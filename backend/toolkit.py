"""Read-only bridge to zhihu-toolkit, with private state per invocation."""
from hashlib import sha256
import json
from contextlib import ExitStack
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit

COLLECTION_LIMIT = 8
COLLECTION_ITEM_LIMIT = 20
TOOLKIT_RUNTIME = Path(__file__).resolve().parents[1] / '.runtime' / 'toolkit-0.5.0'


def account_status(session):
    response = session.get('https://www.zhihu.com/api/v4/me', timeout=10)
    data = response.json() if response.status_code == 200 else {}
    user_id = data.get('id') if isinstance(data, dict) else None
    return {'auth_http_status': response.status_code, 'authenticated': bool(user_id), 'user_id': user_id,
            'username': data.get('name') if user_id else None}


def own_collection_titles(session, expected_account, list_collections, list_contents):
    response = session.get('https://www.zhihu.com/api/v4/me', timeout=10)
    response.raise_for_status()
    identity = response.json()
    user_id = identity.get('id')
    user_token = identity.get('url_token')
    if (not user_id or not isinstance(user_token, str) or not user_token
            or sha256(str(user_id).encode('utf-8')).hexdigest() != expected_account):
        raise RuntimeError('The verified Zhihu account does not match the session')
    folders = list_collections(quote(user_token, safe=''), limit=COLLECTION_LIMIT + 1, max_items=COLLECTION_LIMIT + 1)
    titles = []
    truncated = len(folders) > COLLECTION_LIMIT
    synced = 0
    for folder in folders[:COLLECTION_LIMIT]:
        owner = folder.get('creator_url_token')
        collection_id = str(folder.get('id', ''))
        if (owner and owner != user_token) or not re.fullmatch(r'[0-9]{1,24}', collection_id):
            continue
        items = list_contents(collection_id, limit=COLLECTION_ITEM_LIMIT + 1, max_items=COLLECTION_ITEM_LIMIT + 1)
        truncated = truncated or len(items) > COLLECTION_ITEM_LIMIT
        titles.extend({key: item.get(key) for key in ('type', 'id', 'title')} for item in items[:COLLECTION_ITEM_LIMIT])
        synced += 1
    return {'items': titles, 'collections': synced, 'truncated': truncated}


def browse_answer(url, fetch_json, convert):
    target = urlsplit(url)
    match = re.fullmatch(r'/(?:question/[0-9]+/)?answer/([0-9]{1,24})/?', target.path)
    if target.scheme != 'https' or target.netloc not in {'www.zhihu.com', 'zhihu.com'} or not match:
        raise ValueError('Expected a Zhihu answer URL')
    answer_id = match.group(1)
    item = fetch_json(f'https://www.zhihu.com/api/v4/answers/{answer_id}?include=content')
    if not isinstance(item, dict) or str(item.get('id', '')) != answer_id or 'error' in item:
        raise ValueError('Answer API returned a mismatched item')
    html = item.get('content')
    if not isinstance(html, str) or not html.strip():
        raise ValueError('Answer API returned no body')
    question, author = item.get('question'), item.get('author')
    metadata = {'id': answer_id,
                'title': question.get('title') if isinstance(question, dict) else None,
                'author': author.get('name') if isinstance(author, dict) else None}
    return {'metadata': metadata, 'content_md': convert(html)}


def toolkit_args(args):
    # 0.5 uses its own page size. Both releases accept the total-item cap.
    if tuple(args[:2]) in {('browse', 'feed'), ('browse', 'comments'), ('search', 'article')} and '--limit' in args:
        index = args.index('--limit')
        return [*args[:index], *args[index + 2:]]
    return args


class AccountTransport:
    def __init__(self, cookie):
        self.cookie = cookie

    def run(self, args):
        cookie = self.cookie()
        if not cookie:
            raise RuntimeError('工作台登录已失效，请重新登录。')
        worker = Path(__file__).resolve()
        env = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP', 'LANG') if key in os.environ}
        package = os.environ.get('ZHIHU_WEB_TOOLKIT_PATH')
        if package:
            package_path = Path(package).resolve()
            if not (package_path / 'zhihu_cli' / '__init__.py').is_file():
                raise RuntimeError('工作台的知乎工具目录无效。')
            env['PYTHONPATH'] = str(package_path)
        elif (TOOLKIT_RUNTIME / 'zhihu_cli' / '__init__.py').is_file():
            env['PYTHONPATH'] = str(TOOLKIT_RUNTIME)
        try:
            with TemporaryDirectory(prefix='zhihu-workbench-') as workspace:
                result = subprocess.run(
                    [os.getenv('ZHIHU_WEB_TOOLKIT_PYTHON', sys.executable), '-X', 'utf8', '-B', str(worker),
                     *toolkit_args(args), '--json'],
                    input=cookie, cwd=workspace, env=env, text=True, encoding='utf-8',
                    capture_output=True, timeout=60, check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('知乎工具未响应。') from None
        if result.returncode:
            if re.search(r'\b401\b|the sign-in page', result.stderr, re.I):
                raise RuntimeError('知乎登录已失效，请重新登录。')
            if re.search(r'\b403\b|40362|captcha|account verification|risk control|__zse_ck', result.stderr, re.I):
                raise RuntimeError('知乎拒绝读取或要求验证。')
            raise RuntimeError('知乎工具请求失败。')
        try:
            return json.loads(result.stdout)
        except ValueError:
            raise RuntimeError('知乎工具未返回有效结果。') from None


def worker():
    # This runs in a fresh interpreter. Credentials arrive only on stdin.
    allowed = {('auth', 'status'), ('account', 'collections'), ('browse', 'answer'), ('browse', 'article'),
               ('browse', 'comments'), ('browse', 'feed'), ('search', 'article')}
    if tuple(sys.argv[1:3]) not in allowed:
        raise SystemExit(2)
    cookie = sys.stdin.read(16385)
    if not 0 < len(cookie) <= 16384:
        raise SystemExit(2)

    # Toolkit caches and its optional daemon are shared by default.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from unittest.mock import patch
    with patch.object(Path, 'home', return_value=Path.cwd()):
        from zhihu_cli.content.handlers import requests
        from zhihu_cli.content.handlers._session_core import ZhihuSession, get_browser
        from zhihu_cli.content.handlers.cache_manager import cache_manager

        class AccountHTTP(ZhihuSession):
            def request(self, method, url, **kwargs):
                target = urlsplit(url)
                if (method.upper() != 'GET' or target.scheme != 'https'
                        or target.netloc not in {'www.zhihu.com', 'zhihu.com', 'zhuanlan.zhihu.com', 'api.zhihu.com'}):
                    raise RuntimeError('Unsupported toolkit request')
                kwargs['allow_redirects'] = False
                return super().request(method, url, **kwargs)

        session = AccountHTTP(impersonate=get_browser(''), timeout=20, allow_redirects=False)
        session.captcha_handler = 'ignore'
        if hasattr(session, 'disabled_challenges'):
            from zhihu_cli.content.handlers.challenges import registered

            session.disabled_challenges = {handler.name for handler in registered()}
        headers = {'Cookie': cookie}
        session.headers.update(headers)
        requests._session = session
        requests._headers = dict(headers)
        requests._inject_app_headers(session)
        # CLI releases expose different fields in auth status. Keep this adapter stable.
        if tuple(sys.argv[1:3]) == ('auth', 'status'):
            print(json.dumps(account_status(session), ensure_ascii=False))
            return
        if tuple(sys.argv[1:3]) == ('account', 'collections'):
            if len(sys.argv) != 5 or not re.fullmatch(r'[a-f0-9]{64}', sys.argv[3]):
                raise SystemExit(2)
            from zhihu_cli.content.handlers.collection import list_collection_contents, list_collections

            snapshot = own_collection_titles(session, sys.argv[3], list_collections, list_collection_contents)
            print(json.dumps(snapshot, ensure_ascii=False))
            return
        if tuple(sys.argv[1:3]) == ('browse', 'answer'):
            from zhihu_cli.content.utils.html2markdown import converter

            if len(sys.argv) != 5 or sys.argv[-1] != '--json':
                raise SystemExit(2)
            result = browse_answer(sys.argv[3], requests.fetch_json, converter.convert)
            print(json.dumps(result, ensure_ascii=False))
            return
        with ExitStack() as patches:
            patches.enter_context(patch.object(cache_manager, 'load_headers', return_value=headers))
            if tuple(sys.argv[1:3]) == ('browse', 'feed'):
                from backend.feed_filter import parse_feed_candidate
                from zhihu_cli.content.handlers import feed

                parse_item = feed.parse_feed_item
                patches.enter_context(patch.object(feed, 'parse_feed_item',
                                                   lambda item: parse_feed_candidate(item, parse_item)))
            import click
            from zhihu_cli.commands.browse import register_browse
            from zhihu_cli.commands.search import register_search

            commands = click.Group()
            register_browse(commands)
            register_search(commands)
            commands.main()


if __name__ == '__main__':
    worker()
