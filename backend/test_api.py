"""Exercises the offline task, database and paired evaluation API."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from hashlib import sha256
from zhihu_agent.ranking_eval import evaluation_case

from .accounts import SESSION_COOKIE, SESSION_SECONDS, parse_cookie
from .demo import RESEARCH_QUESTION, run_demo
from .main import ROOT, create_app
from .model_client import ModelConfig
from .store import Store
from .toolkit import own_collection_titles


def synthetic_cohort(directory):
    entries = []
    for index in range(4):
        candidates = [{'candidate_id': f'article:{index * 10 + number}', 'title': title}
                      for number, title in enumerate(('界面布局', '检索日志', '数据库索引', '接口契约', '任务记录'), 1)]
        report = {'run_id': f'test-{index}', 'focus': '检索日志', 'candidates': candidates}
        case = evaluation_case(report)
        labels = {'case_id': case['case_id'], 'label_source': 'ai_proxy', 'annotator': 'test-fixture',
                  'items': [{'candidate_id': row['candidate_id'], 'grade': 2 if index < 2 and i == 1 else 0}
                            for i, row in enumerate(candidates)]}
        entries.append({'run_id': report['run_id'], 'case_id': case['case_id'],
                        'focus_override': report['focus'], 'ai_labels': labels})
        (directory / f"{report['run_id']}.json").write_text(json.dumps(report), encoding='utf-8')
    path = directory / 'cohort.json'
    path.write_text(json.dumps(entries), encoding='utf-8')
    return path


class AccountTests(unittest.TestCase):
    def test_toolkit_release_reads_answers_from_json_and_keeps_total_caps(self):
        from .toolkit import browse_answer, toolkit_args

        calls = []

        def fetch(url):
            calls.append(url)
            return {'id': 42, 'content': '<p>Full answer</p>', 'question': None, 'author': {'name': 'Reader'}}

        result = browse_answer('https://www.zhihu.com/question/12/answer/42', fetch, lambda html: html[3:-4])
        self.assertEqual(calls, ['https://www.zhihu.com/api/v4/answers/42?include=content'])
        self.assertEqual(result, {'metadata': {'id': '42', 'title': None, 'author': 'Reader'}, 'content_md': 'Full answer'})
        with self.assertRaises(ValueError):
            browse_answer('https://www.zhihu.com/answer/41', fetch, str)
        with self.assertRaises(ValueError):
            browse_answer('https://www.zhihu.com/answer/42', lambda _: {'id': 42, 'excerpt': 'preview only'}, str)

        cases = [
            (['browse', 'feed', '--type', 'recommend', '--limit', '18', '-n', '18'],
             ['browse', 'feed', '--type', 'recommend', '-n', '18']),
            (['search', 'article', '--limit', '16', '--max', '16', 'RAG'],
             ['search', 'article', '--max', '16', 'RAG']),
            (['browse', 'comments', 'https://www.zhihu.com/answer/42', '--limit', '10', '--max', '10'],
             ['browse', 'comments', 'https://www.zhihu.com/answer/42', '--max', '10']),
        ]
        for original, compatible in cases:
            self.assertEqual(toolkit_args(original), compatible)

    def test_four_way_evaluation_freezes_order_and_keeps_judgments_out_of_training(self):
        def toolkit(transport, args):
            if args == ['auth', 'status']:
                return {'auth_http_status': 200, 'authenticated': True, 'user_id': transport.cookie().split('=')[-1]}
            if args[:2] == ['account', 'collections']:
                return {'items': [{'type': 'article', 'id': 90, 'title': '事务快照'}],
                        'collections': 1, 'truncated': False}
            return [{'target_type': 'article', 'id': index, 'title': f'候选 {index}'}
                    for index in range(1, 5)]

        def bge(titles):
            return [[1.0, 0.0] if title in {'事务快照', '候选 3'} else [0.0, 1.0] for title in titles]

        def e5(candidates, titles):
            return {item['candidate_id']: {'title': titles['article:90'],
                    'cosine': 0.9 if item['candidate_id'] == 'article:4' else 0.8} for item in candidates}

        with TemporaryDirectory() as folder, patch('backend.embeddings.encode', side_effect=bge), patch(
                'backend.discovery_evaluation.e5_matches', side_effect=e5) as encoder, patch(
                'backend.toolkit.AccountTransport.run', autospec=True, side_effect=toolkit):
            app = create_app(db_path=Path(folder) / 'test.sqlite3')
            headers = {'x-zhihu-workbench': '1'}
            with TestClient(app, headers=headers) as alice, TestClient(app, headers=headers) as bob:
                for browser, name in ((alice, 'alice'), (bob, 'bob')):
                    browser.post('/auth/login', json={'cookie': f'z_c0={name}'}).raise_for_status()
                alice.post('/discover/collections/sync', json={}).raise_for_status()
                batch = alice.get('/discover/articles?auto=true').json()
                path = '/discover/evaluations/' + batch['batch_id']
                initial = alice.post(path).json()
                self.assertEqual([initial['orderings'][name][0] for name in ('zhihu', 'rules', 'bge', 'e5')],
                                 ['article:1', 'article:1', 'article:3', 'article:4'])
                self.assertIsNone(initial['metrics']['e5']['pooled_ndcg_at_3'])
                self.assertEqual(bob.post(path).status_code, 404)
                self.assertEqual(bob.put(path + '/ratings', json={'candidate_id': 'article:4', 'grade': 2}).status_code, 404)
                for index, grade in ((1, 0), (2, 0), (3, 1), (4, 2)):
                    judged = alice.put(path + '/ratings', json={'candidate_id': f'article:{index}', 'grade': grade}).json()
                self.assertEqual(judged['metrics']['e5']['mrr_at_3'], 1)
                self.assertGreater(judged['metrics']['e5']['pooled_ndcg_at_3'], judged['metrics']['zhihu']['pooled_ndcg_at_3'])
                self.assertEqual(judged['orderings'], initial['orderings'])
                self.assertEqual(alice.post(path).json()['grades'], judged['grades'])
                self.assertEqual(alice.get('/discover/articles?auto=true').json()['signals']['human_ratings'], 0)
            encoder.assert_called_once()

    def test_collection_semantics_and_batch_bound_ratings_are_account_local(self):
        def toolkit(transport, args):
            name = transport.cookie().split('=')[-1]
            if args == ['auth', 'status']:
                return {'auth_http_status': 200, 'authenticated': True, 'user_id': name}
            if args[:2] == ['account', 'collections']:
                return {'items': [{'type': 'article', 'id': 12, 'title': '任务检查点'}],
                        'collections': 1, 'truncated': False}
            return [{'target_type': 'article', 'id': 16, 'title': '琐碎经验'},
                    {'target_type': 'article', 'id': 17, 'title': '中断之后继续执行'}]

        def encode(titles):
            return [[0.0, 1.0] if title == '琐碎经验' else [1.0, 0.0] for title in titles]

        with TemporaryDirectory() as folder, patch('backend.embeddings.encode', side_effect=encode), patch(
            'backend.toolkit.AccountTransport.run', autospec=True, side_effect=toolkit,
        ):
            app = create_app(db_path=Path(folder) / 'test.sqlite3')
            headers = {'x-zhihu-workbench': '1'}
            with TestClient(app, headers=headers) as alice, TestClient(app, headers=headers) as bob:
                for browser, name in ((alice, 'alice'), (bob, 'bob')):
                    browser.post('/auth/login', json={'cookie': f'z_c0={name}'}).raise_for_status()
                initial = alice.get('/discover/articles?auto=true').json()
                updated = alice.post('/discover/collections/sync', json={'batch_id': initial['batch_id']}).json()['batch']
                self.assertEqual(updated['ranking_model']['stage'], 'semantic')
                featured = updated['items'][0]
                self.assertEqual((featured['original_rank'], featured['rule_rank'], featured['rank']), (2, 2, 1))
                self.assertEqual(featured['semantic_title'], '任务检查点')
                payload = {'batch_id': updated['batch_id'], 'candidate_id': featured['candidate_id'], 'grade': 2}
                self.assertEqual(bob.put('/discover/rating', json=payload).status_code, 404)
                self.assertEqual(alice.put('/discover/rating', json={**payload, 'candidate_id': 'article:999'}).status_code, 404)
                alice.put('/discover/rating', json=payload).raise_for_status()
                result = alice.put('/discover/rating', json={**payload, 'candidate_id': 'article:16', 'grade': 0}).json()
                self.assertEqual(result['training']['eligible_pairs'], 1)
                self.assertFalse(result['trained_available'])
                undone = alice.put('/discover/rating', json={**payload, 'grade': None}).json()
                self.assertEqual(undone['training']['eligible_pairs'], 0)
                self.assertEqual(bob.get('/discover/articles?auto=true').json()['ranking_model']['stage'], 'rules')

    def test_regularized_ranker_activates_only_after_an_independent_improvement(self):
        from .pairwise_ranker import train

        records = [
            {'batch_id': str(batch), 'goal_key': '', 'cohort_key': str(batch),
             'shown_at': f'{batch:02d}:00', 'rated_at': f'{batch:02d}:10',
             'candidate_id': f'article:{batch}{i}', 'band': 1, 'original_rank': i + 1, 'grade': i,
             'features': [0.05 * (2 - i), 0.0, float(i == 2), float(i == 0), float(i == 1), 1 / (i + 1)]}
            for batch in range(6) for i in range(3)
        ]
        insufficient = train(records[:15])
        self.assertIsNone(insufficient['weights'])
        result = train(records)
        self.assertGreater(result['validation']['learned'], result['validation']['cold_start'])
        self.assertIsNotNone(result['weights'])
        self.assertEqual(result['validation_batches'], 2)
        leaked = [{**row, 'rated_at': '99:00'} for row in records]
        self.assertIsNone(train(leaked)['weights'])

    def test_model_api_keys_are_write_only_and_used_only_by_the_current_account(self):
        from .model_settings import AccountModelKeys

        def auth_status(transport, args):
            account = transport.cookie().split('=')[-1]
            return {'auth_http_status': 200, 'authenticated': True, 'user_id': account, 'username': account}

        global_key = 'test-global-key-must-not-be-used'
        alice_key = 'test-private-alice-key'
        bob_key = 'test-private-bob-key'
        path = '/settings/model'
        headers = {'x-zhihu-workbench': '1'}
        live_task = {'kind': 'research', 'mode': 'live', 'focus': '测试问题', 'research_scope': 'library',
                     'consent': True}
        with (
            TemporaryDirectory() as folder,
            patch.dict(os.environ, {'ZHIHU_WEB_ENABLE_LIVE': '1', 'KIMI_API_KEY': global_key,
                                   'KIMI_BASE_URL': 'https://untrusted.example'}),
            patch('backend.toolkit.AccountTransport.run', autospec=True, side_effect=auth_status),
            patch('backend.library_research.ModelClient') as model,
        ):
            model.return_value.chat.return_value = {'choices': [{'message': {'content': '没有找到相关资料。'}}]}
            app = create_app(db_path=Path(folder) / 'workspace.sqlite3')
            bob = TestClient(app, headers=headers)
            self.addCleanup(bob.close)
            with TestClient(app, headers=headers) as alice:
                self.assertEqual(alice.get(path).status_code, 401)
                alice.post('/auth/login', json={'cookie': 'z_c0=alice'}).raise_for_status()
                bob.post('/auth/login', json={'cookie': 'z_c0=bob'}).raise_for_status()
                self.assertFalse(alice.get(path).json()['configured'])
                self.assertEqual(alice.post('/tasks', json={**live_task, 'nonce': str(uuid4())}).status_code, 403)
                model.assert_not_called()
                saved = alice.put(path, json={'provider': 'openai', 'model': 'gpt-test', 'api_key': alice_key})
                saved.raise_for_status()
                self.assertTrue(saved.json()['live_enabled'])
                self.assertEqual((saved.json()['provider'], saved.json()['model']), ('openai', 'gpt-test'))
                self.assertIn('openrouter', [option['id'] for option in saved.json()['providers']])
                self.assertNotIn(alice_key, alice.get(path).text + saved.text)
                invalid = alice.put(path, json={'api_key': alice_key * 50})
                self.assertEqual(invalid.status_code, 422)
                self.assertNotIn(alice_key, invalid.text)
                self.assertFalse(bob.get(path).json()['live_enabled'])
                self.assertEqual(bob.post('/tasks', json={**live_task, 'nonce': str(uuid4())}).status_code, 403)
                self.assertEqual(alice.post('/tasks', json={**live_task, 'consent': False, 'nonce': str(uuid4())}).status_code, 403)
                model.assert_not_called()
                bob.put(path, json={'provider': 'deepseek', 'api_key': bob_key}).raise_for_status()

                for browser in (alice, bob):
                    response = browser.post('/tasks', json={**live_task, 'nonce': str(uuid4())})
                    self.assertEqual(response.status_code, 202)
                    task_path = '/tasks/' + response.json()['id']
                    for _ in range(70):
                        task = browser.get(task_path).json()
                        if task['status'] not in {'queued', 'running'}:
                            break
                        time.sleep(.015)
                    self.assertNotIn(task['status'], {'queued', 'running'})
                    self.assertNotIn('api_key', task)
                self.assertEqual([call.args for call in model.call_args_list],
                                 [(ModelConfig('openai', 'gpt-test', alice_key),),
                                  (ModelConfig('deepseek', 'deepseek-flash', bob_key),)])

                root = Path(folder) / 'accounts'
                if os.name == 'nt':
                    alice_id = sha256(b'alice').hexdigest()
                    self.assertNotIn(alice_key.encode(), (root / alice_id / 'model.dpapi').read_bytes())
                    self.assertEqual(AccountModelKeys(root).get(alice_id), ModelConfig('openai', 'gpt-test', alice_key))
                self.assertFalse(alice.delete(path).json()['configured'])
                self.assertTrue(bob.get(path).json()['configured'])
                self.assertEqual(alice.post('/tasks', json={**live_task, 'nonce': str(uuid4())}).status_code, 403)

    def test_model_client_sends_targeted_requests_and_preserves_tool_calls(self):
        from io import BytesIO
        from .model_client import ModelClient

        client = ModelClient(ModelConfig('openai', 'gpt-test', 'fixture-secret'))
        tools = [{'type': 'function', 'function': {'name': 'read_answer'}}]
        call = {'id': 'tool-1', 'type': 'function',
                'function': {'name': 'read_answer', 'arguments': '{}'}, 'extra_content': {'test': 'signature'}}
        payload = {'choices': [{'message': {'tool_calls': [call]}}]}
        with patch.object(client.opener, 'open', return_value=BytesIO(json.dumps(payload).encode())) as send:
            self.assertEqual(client.chat([{'role': 'user', 'content': 'Read this'}], tools), payload)
            request = send.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.openai.com/v1/chat/completions')
            self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-secret')
            data = json.loads(request.data)
            self.assertEqual(data['model'], 'gpt-test')
            self.assertEqual(data['max_completion_tokens'], 1800)
            self.assertEqual(data['tools'], tools)

    def test_feed_filters_promotions_and_hides_only_for_the_current_account(self):
        from .feed_filter import parse_feed_candidate

        article = {'id': 42, 'target_type': 'article', 'title': '广告投放系统的技术原理'}
        raw_ad = {'target': {'type': 'article', 'is_sponsored': True}}
        with patch('backend.main.ModelClient') as model:
            def toolkit(transport, args):
                if args == ['auth', 'status']:
                    account = transport.cookie().split('=')[-1]
                    return {'auth_http_status': 200, 'authenticated': True, 'user_id': account}
                return [parse_feed_candidate(raw_ad, lambda _: article),
                        {'id': 41, 'target_type': 'article', 'title': '【商业推广】试用产品'},
                        article]

            headers = {'x-zhihu-workbench': '1'}
            feed_path = '/discover/articles?auto=true'
            with TemporaryDirectory() as folder, patch(
                'backend.toolkit.AccountTransport.run', autospec=True, side_effect=toolkit,
            ):
                app = create_app(db_path=Path(folder) / 'workspace.sqlite3')
                with TestClient(app, headers=headers) as alice, TestClient(app, headers=headers) as bob:
                    alice.post('/auth/login', json={'cookie': 'z_c0=alice'}).raise_for_status()
                    bob.post('/auth/login', json={'cookie': 'z_c0=bob'}).raise_for_status()
                    result = alice.get(feed_path).json()
                    self.assertEqual(result['filtered_ads'], 2)
                    self.assertEqual([row['candidate_id'] for row in result['items']], ['article:42'])
                    visibility = {'candidate_id': 'article:42', 'hidden': True}
                    alice.put('/discover/visibility', json=visibility).raise_for_status()
                    self.assertEqual(alice.get(feed_path).json()['items'], [])
                    reranked = alice.post(f"/discover/batches/{result['batch_id']}/rerank").json()
                    self.assertEqual(reranked['items'], [])
                    self.assertEqual(len(bob.get(feed_path).json()['items']), 1)
                    alice.put('/discover/visibility', json={**visibility, 'hidden': False}).raise_for_status()
                    self.assertEqual(len(alice.get(feed_path).json()['items']), 1)
            model.assert_not_called()

    def test_collection_worker_checks_the_verified_owner_and_only_reads_owned_folders(self):
        from unittest.mock import Mock

        session = Mock()
        session.get.return_value.json.return_value = {'id': 'owner-1', 'url_token': 'owner'}
        account_key = sha256(b'owner-1').hexdigest()
        folders = Mock(return_value=[{'id': '1', 'creator_url_token': 'owner'},
                                     {'id': '2', 'creator_url_token': 'someone-else'}])
        contents = Mock(return_value=[{'type': 'answer', 'id': 99, 'title': '我的收藏', 'excerpt': '不保存'}])
        result = own_collection_titles(session, account_key, folders, contents)
        self.assertEqual(result, {'items': [{'type': 'answer', 'id': 99, 'title': '我的收藏'}],
                                  'collections': 1, 'truncated': False})
        self.assertEqual(contents.call_args.args[0], '1')
        with self.assertRaises(RuntimeError):
            own_collection_titles(session, 'a' * 64, folders, contents)
        folders.assert_called_once()
        contents.assert_called_once()

    def test_zhihu_collections_rerank_one_fixed_batch_and_stay_in_the_signed_in_account(self):
        def toolkit(transport, args):
            name = transport.cookie().split('=')[-1]
            if args == ['auth', 'status']:
                return {'auth_http_status': 200, 'authenticated': True, 'user_id': name, 'username': name}
            if args[:2] == ['account', 'collections']:
                self.assertEqual(args[2], sha256(name.encode()).hexdigest())
                title = '数据库快照恢复详细步骤' if name == 'alpha' else '预算规划与风险管理手册'
                item = {'type': 'answer', 'id': 90, 'title': title}
                return {'items': [item, item], 'collections': 1, 'truncated': False}
            return [{'target_type': 'article', 'id': '11', 'title': 'Linux 进程排查笔记'},
                    {'target_type': 'article', 'id': '12', 'title': '数据库快照恢复步骤'},
                    {'target_type': 'article', 'id': '13', 'title': '预算规划与风险管理'}]

        with TemporaryDirectory() as folder, patch('backend.toolkit.AccountTransport.run', autospec=True, side_effect=toolkit):
            app = create_app(db_path=Path(folder) / 'workspace.sqlite3')
            headers = {'x-zhihu-workbench': '1'}
            path = '/discover/collections/sync'
            beta = TestClient(app, headers=headers)
            self.addCleanup(beta.close)
            with TestClient(app, headers=headers) as alpha:
                self.assertEqual(alpha.post(path, json={}).status_code, 401)
                alpha.post('/auth/login', json={'cookie': 'z_c0=alpha'}).raise_for_status()
                beta.post('/auth/login', json={'cookie': 'z_c0=beta'}).raise_for_status()
                snapshot = alpha.get('/discover/articles?auto=true').json()
                beta_snapshot = beta.get('/discover/articles?auto=true').json()
                self.assertEqual([item['candidate_id'] for item in snapshot['items']], ['article:11', 'article:12', 'article:13'])
                synced = alpha.post(path, json={'batch_id': snapshot['batch_id']}).json()
                ranks = {item['candidate_id']: item for item in synced['comparison']}
                self.assertEqual((ranks['article:12']['before'], ranks['article:12']['after']), (2, 1))
                self.assertEqual(ranks['article:12']['favorite_title'], '数据库快照恢复详细步骤')
                self.assertGreater(ranks['article:12']['bonus'], 0)
                self.assertEqual(synced['zhihu_collections']['count'], 1)
                self.assertEqual(alpha.get('/discover/articles?auto=true').json()['items'][0]['candidate_id'], 'article:12')
                self.assertIsNone(beta.get('/discover/articles?auto=true').json()['zhihu_collections'])
                beta_synced = beta.post(path, json={'batch_id': beta_snapshot['batch_id']}).json()
                self.assertEqual(beta_synced['batch']['items'][0]['candidate_id'], 'article:13')
                self.assertNotIn('数据库快照恢复详细步骤', json.dumps(beta_synced, ensure_ascii=False))
                focused = beta.get('/discover/articles', params={'auto': True, 'goal': 'Linux 进程排查'}).json()
                self.assertEqual(focused['items'][0]['candidate_id'], 'article:11')
                self.assertEqual(beta.post(path, json={'batch_id': snapshot['batch_id']}).json()['comparison'], [])

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI session persistence')
    def test_windows_session_survives_restart_renews_and_stays_revoked_after_logout(self):
        cookie = 'z_c0=encrypted-session-test'
        headers = {'x-zhihu-workbench': '1'}
        with TemporaryDirectory() as folder, patch('backend.accounts.verify_account', return_value=('test-account', '验收账号')):
            path = Path(folder) / 'workspace.sqlite3'
            with TestClient(create_app(db_path=path), headers=headers) as first:
                response = first.post('/auth/login', json={'cookie': cookie})
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'Max-Age={SESSION_SECONDS}', response.headers['set-cookie'])
                first.post('/library', json={'source': 'note', 'title': '本机文章', 'body': '正文'}).raise_for_status()
                token = first.cookies[SESSION_COOKIE]
            saved = (path.parent / 'accounts' / 'sessions.dpapi').read_bytes()
            self.assertNotIn(cookie.encode(), saved)
            self.assertNotIn(token.encode(), saved)
            with TestClient(create_app(db_path=path), headers=headers, cookies={SESSION_COOKIE: token}) as restarted:
                self.assertEqual(restarted.get('/library').json()[0]['title'], '本机文章')
                with patch('backend.accounts.time', return_value=time.time() + 2 * 24 * 3600):
                    renewed = restarted.get('/auth/session')
                    self.assertEqual(renewed.json()['user']['name'], '验收账号')
                    self.assertIn(f'Max-Age={SESSION_SECONDS}', renewed.headers['set-cookie'])
                self.assertEqual(restarted.post('/auth/logout').status_code, 204)
            with TestClient(create_app(db_path=path), cookies={SESSION_COOKIE: token}) as logged_out:
                self.assertEqual(logged_out.get('/auth/session').json(), {'user': None})
                self.assertEqual(logged_out.get('/library').status_code, 401)

    def test_zhihu_401_does_not_end_workbench_session(self):
        with (
            TemporaryDirectory() as folder,
            patch('backend.accounts.verify_account', return_value=('test-account', '验收账号')),
            patch('backend.toolkit.AccountTransport.run', side_effect=RuntimeError('知乎登录已失效，请重新登录。')),
            TestClient(create_app(db_path=Path(folder) / 'workspace.sqlite3'),
                       headers={'x-zhihu-workbench': '1'}) as client,
        ):
            client.post('/auth/login', json={'cookie': 'z_c0=fake'}).raise_for_status()
            response = client.get('/discover/articles?auto=true')
            self.assertEqual(response.status_code, 424)
            self.assertIn('知乎未接受', response.json()['detail'])
            self.assertEqual(client.get('/library').status_code, 200)
            self.assertEqual(client.get('/auth/session').json()['user']['name'], '验收账号')

    def test_cookie_header_copies_keep_values_and_distinguish_bad_input(self):
        header = 'd_c0="device=="; z_c0=session==; _xsrf=token'
        for copied in (header, 'Cookie: ' + header, 'Cookie\r\n' + header,
                       '\ufeffcookie:\r\n d_c0="device==";\r\n z_c0 = session==;\n _xsrf=token'):
            with self.subTest(copied=copied):
                self.assertEqual(parse_cookie(copied), header)
        for copied, message in (
            ('d_c0=fake; _xsrf=token', '没有有效的 z_c0'),
            ('z_c0=" "', '没有有效的 z_c0'),
            ('z_c0=fake\r\nAnother-Header: value', '格式不对'),
            ('Set-Cookie: z_c0=fake; HttpOnly', '格式不对'),
        ):
            with self.subTest(copied=copied), self.assertRaises(HTTPException) as error:
                parse_cookie(copied)
            self.assertEqual(error.exception.status_code, 422)
            self.assertIn(message, error.exception.detail)
            self.assertNotIn(copied, error.exception.detail)

    def test_login_binds_gateway_and_private_data_to_verified_account(self):
        old_cookie, new_cookie = 'z_c0=test-first', 'z_c0=test-second'

        def toolkit(transport, args):
            cookie = transport.cookie()
            if args == ['auth', 'status']:
                if cookie not in {old_cookie, new_cookie}:
                    return {'auth_http_status': 401, 'authenticated': False}
                user_id = 'account-a' if cookie == old_cookie else 'account-b'
                return {'auth_http_status': 200, 'authenticated': True, 'user_id': user_id, 'username': user_id}
            if args[:2] == ['browse', 'article']:
                self.assertEqual(cookie, old_cookie)
                return {'metadata': {'id': '30', 'title': '账号 A 的文档'}, 'content_md': '仅供当前账号的证据。'}
            self.assertEqual(args[:2], ['browse', 'feed'])
            return [{'id': '31', 'target_type': 'article', 'title': '甲的推荐' if cookie == old_cookie else '乙的推荐'}]

        with TemporaryDirectory() as folder, patch('backend.toolkit.AccountTransport.run', autospec=True, side_effect=toolkit):
            store_path = Path(folder) / 'workspace.sqlite3'
            Store(store_path).init()
            with Store(store_path).connect() as legacy:
                legacy.execute("INSERT INTO reading_library VALUES ('demo','旧阅读库','','demo',NULL,'旧资料','2026-01-01')")
            headers = {'x-zhihu-workbench': '1'}
            app = create_app(db_path=store_path)
            browser_b = TestClient(app, headers=headers)
            self.addCleanup(browser_b.close)
            with TestClient(app, headers=headers) as browser_a:
                self.assertEqual(browser_a.get('/library').status_code, 401)
                self.assertEqual(browser_a.get('/discover/articles?auto=true').status_code, 401)
                invalid = browser_a.post('/auth/login', json={'cookie': 'z_c0=expired'})
                self.assertEqual(invalid.status_code, 401)
                self.assertNotIn('expired', invalid.text)
                malformed = browser_a.post('/auth/login', json={'cookie': 'z_c0=' + 'sensitive' * 2048})
                self.assertEqual(malformed.status_code, 422)
                self.assertNotIn('sensitive', malformed.text)
                logged_in = browser_a.post('/auth/login', json={'cookie': 'Cookie\r\n' + old_cookie})
                self.assertEqual(logged_in.status_code, 200, logged_in.text)
                self.assertNotIn(old_cookie, logged_in.text + logged_in.headers['set-cookie'])
                self.assertIn('HttpOnly', logged_in.headers['set-cookie'])
                self.assertIn('SameSite=strict', logged_in.headers['set-cookie'])
                token_a = browser_a.cookies[SESSION_COOKIE]
                self.assertEqual(browser_a.get('/library').json(), [])
                self.assertEqual(browser_a.get('/discover/articles?auto=true').json()['items'][0]['title'], '甲的推荐')
                preview = browser_a.get('/library/preview', params={'url': 'https://zhuanlan.zhihu.com/p/30'}).json()
                payload = {'source': 'zhihu', 'preview_id': preview['preview_id']}
                preview_questions = {'preview_id': preview['preview_id']}
                self.assertIn('账号 A 的文档', browser_a.get('/library/questions', params=preview_questions).json()['questions'][0]['text'])
                browser_a.post('/library', json=payload).raise_for_status()
                self.assertEqual(browser_a.get('/discover/articles?auto=true').json()['goal_suggestion'],
                                 {'text': '账号 A 的文档', 'source': 'history', 'signal': 'library', 'title': '账号 A 的文档'})
                task = browser_a.post('/tasks', json={'nonce': str(uuid4()), 'focus': 'Agent 工具调用'}).json()
                browser_a.put('/discover/rating', json={
                    'candidate_id': 'article:31', 'title': '甲的推荐', 'grade': 2,
                }).raise_for_status()
                browser_b.post('/auth/login', json={'cookie': new_cookie}).raise_for_status()
                self.assertEqual(browser_b.get('/library').json(), [])
                self.assertEqual(browser_b.get('/library/article-30').status_code, 404)
                self.assertEqual(browser_b.get('/library/questions', params=preview_questions).status_code, 410)
                self.assertEqual(browser_b.get('/tasks').json(), [])
                self.assertEqual(browser_b.get('/tasks/' + task['id']).status_code, 404)
                self.assertEqual(browser_b.post('/library', json=payload).status_code, 410)
                discovery_b = browser_b.get('/discover/articles?auto=true').json()
                self.assertEqual(discovery_b['items'][0]['title'], '乙的推荐')
                self.assertIsNone(discovery_b['items'][0]['my_grade'])
                self.assertEqual(discovery_b['goal_suggestion'], {'text': '', 'source': 'none'})
                self.assertEqual(browser_a.post('/auth/logout').status_code, 204)
                self.assertEqual(browser_a.get('/auth/session').json(), {'user': None})
                browser_a.cookies.set(SESSION_COOKIE, token_a)
                self.assertEqual(browser_a.get('/library').status_code, 401)
                browser_a.cookies.clear()
                browser_a.post('/auth/login', json={'cookie': old_cookie}).raise_for_status()
                self.assertEqual(browser_a.get('/library/article-30').status_code, 200)
                self.assertEqual(browser_a.get('/discover/articles?auto=true').json()['items'][0]['my_grade'], 2)
            for db in Path(folder).rglob('*.sqlite3'):
                self.assertNotIn(old_cookie.encode(), db.read_bytes())
                self.assertNotIn(new_cookie.encode(), db.read_bytes())


class ApiTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.runtime'
        scratch.mkdir(exist_ok=True)
        temp = self.enterContext(TemporaryDirectory(dir=scratch))
        self.db = Path(temp) / 'test.sqlite3'
        self.enterContext(patch.dict(os.environ, {'ZHIHU_WEB_ENABLE_LIVE': '0',
                                                'ZHIHU_AGENT_DATA_DIR': str(self.db.parent / 'answers')}))
        cohort = synthetic_cohort(self.db.parent)
        self.client = self.enterContext(TestClient(create_app(db_path=self.db, cohort_file=cohort,
                                                              run_dir=self.db.parent, require_login=False)))
        self.client.headers['x-zhihu-workbench'] = '1'

    def test_demo_executes_core_and_keeps_reopenable_result(self):
        payload = {'nonce': str(uuid4()), 'focus': 'Agent 工具调用 证据校验', 'mode': 'demo'}
        response = self.client.post('/tasks', json=payload)
        self.assertEqual(response.status_code, 202)
        task_id = response.json()['id']
        for _ in range(60):
            task = self.client.get(f'/tasks/{task_id}').json()
            if task['status'] not in {'queued', 'running'}:
                break
            time.sleep(.015)
        self.assertEqual(task['status'], 'completed', task)
        self.assertEqual([row['stage'] for row in task['events']],
                         ['queued', 'fetch', 'plan', 'execute', 'validate', 'reflect', 'publish', 'end', 'completed'])
        result = task['result']
        self.assertTrue(result['reviews'][0]['approved'])
        self.assertEqual(result['candidate_ranking']['items'][0]['home_rank'], 2)
        self.assertEqual(result['candidate_ranking']['items'][0]['focus_rank'], 1)
        self.assertFalse(result['usage_complete'])
        self.assertIn('CITES', {row['relation'] for row in result['graph']['edges']})
        self.assertNotIn('report_path', result)
        self.assertEqual(self.client.post('/tasks', json=payload).json()['id'], task_id)
        self.assertEqual(len(self.client.get('/tasks').json()), 1)
        self.assertEqual(Store(self.db).task(task_id)['result']['run_id'], result['run_id'])
        self.assertEqual(self.client.post('/tasks', json={**payload, 'focus': 'different'}).status_code, 409)

    def test_question_suggestions_follow_the_saved_material_and_scope(self):
        path = '/library/questions'
        self.assertEqual(self.client.get(path).json(), {'questions': []})
        passage = '渐进退避流程按重试次数延长等待时间，服务恢复后清零计数。'
        first = self.client.post('/library', json={
            'source': 'note', 'title': '任务队列设计',
            'body': '## 渐进退避流程\n\n' + passage + '\n\n## 重试上限注意事项\n\n超出上限时将任务移到待处理队列。',
        }).json()
        second = self.client.post('/library', json={
            'source': 'note', 'title': '数据库快照', 'body': '## 快照恢复步骤\n\n快照恢复需要先检查文件完整性。',
        }).json()
        scoped = self.client.get(path, params={'item_id': first['id']}).json()['questions']
        self.assertTrue(all(row['source_title'] == first['title'] for row in scoped))
        self.assertIn('渐进退避流程', scoped[1]['text'])
        answer = self.client.post(f"/library/{first['id']}/ask", json={'question': scoped[1]['text']}).json()
        self.assertIn(passage, answer['sources'][0]['quotes'])
        self.assertEqual({row['source_title'] for row in self.client.get(path).json()['questions']},
                         {first['title'], second['title']})
        self.client.delete(f"/library/{first['id']}").raise_for_status()
        self.assertEqual(self.client.get(path, params={'item_id': first['id']}).status_code, 404)
        self.assertTrue(all(row['source_title'] == second['title'] for row in self.client.get(path).json()['questions']))

    def test_library_keeps_one_local_snapshot_and_reads_comments_on_demand(self):
        url = 'https://www.zhihu.com/question/20/answer/30'
        with patch('backend.library.ZhihuGateway.run', side_effect=[
            {'metadata': {'id': 30, 'title': '回答标题', 'author': '作者'}, 'content_md': '正文'},
            [{'author': '读者', 'content': '<script>示例文本</script>', 'child_comments': [{'content': '回复'}]}],
        ]) as cli:
            params = {'source': 'zhihu', 'url': url + '?ignore=1'}
            added = self.client.post('/library', json=params)
            self.assertEqual(added.status_code, 201)
            item_path = '/library/' + added.json()['id']
            self.assertEqual(self.client.post('/library', json=params).json(), added.json())
            self.assertEqual(self.client.get(item_path).json()['body'], '正文')
            self.assertEqual(cli.call_count, 1)
            self.assertEqual(cli.call_args.args[0], ['browse', 'answer', url])
            self.assertNotIn('body', self.client.get('/library').json()[0])
            comments = self.client.get(item_path + '/comments').json()['comments']
            self.assertEqual(comments[0]['replies'][0]['content'], '回复')
            self.assertEqual(cli.call_args.args[0][:3], ['browse', 'comments', url])
            self.assertEqual(self.client.delete(item_path, headers={'x-zhihu-workbench': '0'}).status_code, 403)
            self.assertEqual(self.client.delete(item_path).status_code, 204)
            self.assertEqual(self.client.get(item_path).status_code, 404)
            self.assertEqual(self.client.get('/library').json(), [])
            self.assertEqual(cli.call_count, 2)

    def test_library_rejects_foreign_urls_and_mismatched_zhihu_content(self):
        with patch('backend.library.ZhihuGateway.run', return_value={'metadata': {'id': 6}, 'content_md': '正文'}) as cli:
            self.assertEqual(self.client.post('/library', json={'source': 'zhihu', 'url': 'https://www.zhihu.com.evil.invalid/answer/5'}).status_code, 422)
            cli.assert_not_called()
            self.assertEqual(self.client.post('/library', json={'source': 'zhihu', 'url': 'https://zhuanlan.zhihu.com/p/5'}).status_code, 502)
            self.assertEqual(self.client.get('/library').json(), [])

    def test_preview_reads_and_cites_without_saving_or_calling_a_model(self):
        url = 'https://www.zhihu.com/question/20/answer/30'
        text = '证据校验需要检查每一条引用能否在原文中逐字找到。'
        article = {'metadata': {'id': 30, 'title': '证据校验'}, 'content_md': text}
        with patch('backend.library.ZhihuGateway.run', side_effect=[article, [{'content': '评论正文'}]]) as cli:
            preview = self.client.get('/library/preview', params={'url': url}).json()
            self.assertFalse(preview['saved'])
            self.assertEqual(preview['body'], text)
            comments = self.client.get('/library/preview/comments', params={'url': url}).json()
            self.assertEqual(comments['comments'][0]['content'], '评论正文')
            payload = {'preview_id': preview['preview_id'], 'question': '证据校验'}
            result = self.client.post('/library/preview/ask', json=payload).json()
            self.assertEqual(result['sources'][0]['quotes'], [text])
            self.assertNotIn('library_item_id', result['sources'][0])
            self.assertEqual(self.client.get('/library').json(), [])
            self.assertEqual(self.client.post('/library/preview/ask', json={**payload, 'body': '伪造正文'}).status_code, 422)
            self.assertEqual(self.client.post('/library/preview/ask', json={**payload, 'preview_id': '0' * 32}).status_code, 410)
            item = self.client.post('/library', json={'source': 'zhihu', 'preview_id': payload['preview_id']}).json()
            self.assertTrue(self.client.get('/library/preview', params={'url': url}).json()['saved'])
            saved = self.client.post(f"/library/{item['id']}/ask", json={'question': '证据校验'}).json()
            self.assertEqual(saved['sources'][0]['library_item_id'], item['id'])
            self.assertEqual(cli.call_count, 2)

    def test_discover_search_preserves_baseline_and_explains_goal_rerank(self):
        rows = [
            {'id': '1', 'title': 'Agent 原理详解', 'excerpt': '智能体的基本概念。'},
            {'id': '2', 'title': '<em>Agent</em> 工程实战', 'excerpt': '使用 Python 状态机处理失败重试。'},
            {'id': '2', 'title': '重复内容'},
        ]
        with patch('backend.discover.cli_read', return_value=rows) as search:
            response = self.client.get('/discover/articles', params={
                'topic': 'agent', 'goal': 'Python 状态机', 'reading_type': 'tutorial',
            })
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload['query'], 'Agent 工程实践')
            first, second = payload['items']
            self.assertEqual((first['original_rank'], first['rank']), (2, 1))
            self.assertEqual(first['title'], 'Agent 工程实战')
            self.assertEqual(first['topic'], 'agent')
            self.assertTrue(first['format_match'])
            self.assertEqual(second['reading_type'], 'principle')
            self.assertEqual(first['url'], 'https://zhuanlan.zhihu.com/p/2')
            self.assertEqual(self.client.get('/discover/articles', params={'q': '--max'}).status_code, 422)
            search.assert_called_once()

    def test_discover_applies_human_grades_without_overriding_clear_goal_matches(self):
        rows = [
            {'id': '101', 'title': 'Python 状态机', 'excerpt': '任务失败重试。'},
            {'id': '102', 'title': 'Vue 界面实践', 'excerpt': '前端工程。'},
            {'id': '103', 'title': 'Python 状态机', 'excerpt': '任务失败重试。'},
        ]
        with patch('backend.discover.cli_read', return_value=rows), patch('backend.discover_preferences.favorites_for', return_value=None):
            for article_id, grade in [('101', 0), ('102', 2), ('103', 2)]:
                response = self.client.put('/discover/rating', json={
                    'candidate_id': f'article:{article_id}', 'title': article_id, 'grade': grade,
                })
                self.assertEqual(response.status_code, 200, response.text)
            result = self.client.get('/discover/articles', params={'q': '工程', 'goal': 'Python 状态机'}).json()
            self.assertEqual([row['candidate_id'] for row in result['items']], ['article:103', 'article:101', 'article:102'])
            self.assertEqual([row['baseline_rank'] for row in result['items']], [2, 1, 3])
            self.assertEqual(result['signals']['human_ratings'], 3)
            self.client.put('/discover/rating', json={'candidate_id': 'article:103', 'title': '103', 'grade': None})
            again = self.client.get('/discover/articles', params={'q': '工程', 'goal': 'Python 状态机'}).json()
            self.assertIsNone(again['items'][0]['my_grade'])

    def test_new_feed_penalizes_only_close_dislikes_and_respects_explicit_goal(self):
        disliked = '减脂停滞：压力性进食与睡眠不足'
        rows = [
            {'id': '601', 'target_type': 'article', 'title': '减脂停滞和压力性进食、睡眠不足有关吗？'},
            {'id': '602', 'target_type': 'article', 'title': '减脂期如何制定每周运动计划'},
            {'id': '603', 'target_type': 'article', 'title': '数据库索引维护手册'},
        ]
        with patch('backend.discover.cli_read', return_value=rows):
            rated = {'candidate_id': 'article:600', 'title': disliked, 'grade': 0}
            self.client.put('/discover/rating', json=rated).raise_for_status()
            with patch('backend.discover.RANKING_VERSION', 'discovery-v3'):
                before = self.client.get('/discover/articles?auto=true').json()
            after = self.client.get('/discover/articles?auto=true').json()
            self.assertEqual([row['candidate_id'] for row in before['items']], ['article:601', 'article:602', 'article:603'])
            self.assertEqual([row['candidate_id'] for row in after['items']], ['article:602', 'article:603', 'article:601'])
            same_theme, _, similar = after['items']
            self.assertEqual(same_theme['dislike_penalty'], 0)
            self.assertEqual(similar['dislike_title'], disliked)
            self.assertGreater(similar['dislike_penalty'], 0)
            self.assertLessEqual(similar['dislike_penalty'], 0.12)

            self.client.put('/discover/rating', json={**rated, 'candidate_id': 'article:604'}).raise_for_status()
            more = self.client.get('/discover/articles?auto=true').json()
            self.assertEqual(more['items'][-1]['dislike_penalty'], similar['dislike_penalty'])
            focused = self.client.get('/discover/articles', params={'auto': True, 'goal': '压力性进食'}).json()
            self.assertEqual(focused['items'][0]['candidate_id'], 'article:601')
            self.assertGreater(focused['items'][0]['dislike_penalty'], 0)

            self.client.put('/discover/rating', json={
                'candidate_id': 'article:601', 'title': rows[0]['title'], 'grade': 2,
            }).raise_for_status()
            explicit = self.client.get('/discover/articles?auto=true').json()['items'][0]
            self.assertEqual(explicit['candidate_id'], 'article:601')
            self.assertEqual(explicit['rating_score'], 0.2)
            self.assertEqual(explicit['dislike_penalty'], 0)

    def test_discovery_suggests_from_query_and_related_saved_title_but_preserves_search_priority(self):
        title = 'RAG 检索评测中的证据校验'
        self.client.post('/library', json={'source': 'note', 'title': title, 'body': '内容示例'}).raise_for_status()
        rows = [
            {'id': '11', 'title': '证据校验与引用'},
            {'id': '12', 'title': 'RAG 检索评测'},
        ]
        with (
            patch('backend.discover.cli_read', return_value=rows) as gateway,
            patch('backend.discover_preferences.favorites_for', return_value=None),
            patch('backend.discover_preferences.ReadingStore.events', return_value=[]),
        ):
            result = self.client.get('/discover/articles', params={'q': 'RAG 检索评测'}).json()
            self.assertEqual(result['goal_suggestion']['title'], title)
            self.assertEqual(result['goal_suggestion']['signal'], 'library')
            self.assertIn(title, result['goal'])
            self.assertEqual(result['goal_origin'], 'query')
            self.assertEqual(result['items'][0]['candidate_id'], 'article:12')
            self.assertIn('RAG 检索评测', gateway.call_args.args[0])
            self.assertNotIn(title, gateway.call_args.args[0])

            manual = self.client.get('/discover/articles', params={'q': 'RAG 检索评测', 'goal': '证据校验'}).json()
            self.assertEqual(manual['goal_origin'], 'manual')
            self.assertEqual(manual['goal'], '证据校验')
            self.assertEqual(manual['items'][0]['candidate_id'], 'article:11')

    def test_discovery_feed_goal_uses_current_data_and_preserves_a_frozen_batch(self):
        rows = [
            {'id': '21', 'target_type': 'article', 'title': 'Python Agent 工程'},
            {'id': '22', 'target_type': 'article', 'title': 'LangGraph 智能体实践'},
        ]
        with (
            patch('backend.discover.cli_read', return_value=rows) as gateway,
            patch('backend.discover_preferences.favorites_for', return_value=None),
            patch('backend.discover_preferences.ReadingStore.events', return_value=[]),
        ):
            cold = self.client.get('/discover/articles?auto=true').json()
            self.assertEqual(cold['goal_suggestion'], {'text': 'Agent 工程', 'source': 'feed', 'count': 2})
            path = f"/discover/batches/{cold['batch_id']}/rerank"
            retargeted = self.client.post(path, params={'goal': 'LangGraph 智能体实践'}).raise_for_status().json()
            self.assertNotEqual(retargeted['batch_id'], cold['batch_id'])
            self.assertEqual(retargeted['goal_origin'], 'manual')
            self.assertEqual(retargeted['items'][0]['candidate_id'], 'article:22')
            self.assertEqual({item['candidate_id'] for item in retargeted['items']},
                             {item['candidate_id'] for item in cold['items']})
            restored = self.client.post(f"/discover/batches/{retargeted['batch_id']}/rerank",
                                        params={'goal': ''}).raise_for_status().json()
            self.assertEqual(restored['goal'], cold['goal'])
            self.assertEqual(restored['goal_origin'], 'feed')
            gateway.assert_called_once()
            title = 'LangGraph 智能体实践'
            self.client.put('/discover/rating', json={'candidate_id': 'article:22', 'title': title, 'grade': 2}).raise_for_status()
            updated = self.client.get('/discover/articles?auto=true').json()
            self.assertEqual(updated['goal_suggestion'], {'text': title, 'source': 'history', 'signal': 'rating', 'title': title})
            frozen = self.client.post(f"/discover/batches/{cold['batch_id']}/rerank").json()
            self.assertEqual(frozen['goal_suggestion'], cold['goal_suggestion'])

    def test_feedback_reranks_one_frozen_batch_without_using_evaluation_labels(self):
        rows = [
            {'id': '401', 'title': 'Agent 工程甲'},
            {'id': '402', 'title': 'Agent 工程乙'},
        ]
        with patch('backend.discover.cli_read', return_value=rows) as gateway, \
                patch('backend.discover_preferences.favorites_for', return_value=None) as favorites:
            case = self.client.get('/lab/cases').json()[0]
            path = f"/lab/cases/{case['case_id']}"
            items = self.client.get(path).json()['items']
            self.client.put(path + '/ratings', json={'items': [
                {'candidate_id': item['candidate_id'], 'grade': 2} for item in items
            ]}).raise_for_status()
            initial = self.client.get('/discover/articles', params={'q': 'Agent 工程'}).json()
            self.assertEqual(initial['signals']['human_ratings'], 0)
            url = f"/discover/batches/{initial['batch_id']}/rerank"
            feedback = {'candidate_id': 'article:402', 'title': rows[1]['title'], 'grade': 2}
            self.client.put('/discover/rating', json=feedback).raise_for_status()
            updated = self.client.post(url).json()
            top = updated['items'][0]
            self.assertEqual(top['candidate_id'], 'article:402')
            self.assertEqual((top['initial_rank'], top['rank'], top['baseline_rank']), (2, 1, 2))
            self.assertEqual(updated['batch_id'], initial['batch_id'])
            self.client.put('/discover/rating', json={**feedback, 'grade': None}).raise_for_status()
            self.assertEqual(self.client.post(url).json()['items'][0]['candidate_id'], 'article:401')
            gateway.assert_called_once()
            favorites.assert_called_once()
            self.assertEqual(self.client.post('/discover/batches/' + '0' * 32 + '/rerank').status_code, 404)

    def test_missing_eval_archive_does_not_block_reading(self):
        with TestClient(create_app(db_path=self.db.parent / 'partial.sqlite3',
                                   cohort_file=self.db.parent / 'missing.json', require_login=False)) as client:
            self.assertEqual(client.get('/health').json()['status'], 'ok')
            self.assertEqual(client.get('/library').json(), [])
            self.assertEqual(client.get('/lab/cases').status_code, 503)

    def test_library_research_cites_saved_text_and_stops_finding_deleted_rows(self):
        body = '蓝鲸验证方案要求每条测试记录附上精确的原文引用。'
        item = self.client.post('/library', json={'source': 'note', 'title': '蓝鲸验证方案', 'body': body}).json()
        request = {'focus': '蓝鲸验证方案', 'kind': 'research', 'research_scope': 'library'}

        def run_question(**scope):
            response = self.client.post('/tasks', json={**request, **scope, 'nonce': str(uuid4())})
            self.assertEqual(response.status_code, 202, response.text)
            path = '/tasks/' + response.json()['id']
            for _ in range(80):
                task = self.client.get(path).json()
                if task['status'] not in {'queued', 'running'}:
                    break
                time.sleep(.015)
            self.assertEqual(task['status'], 'completed', task)
            return task['result']

        with patch('backend.library_research.ModelClient') as model:
            result = run_question(library_item_id=item['id'])
            self.assertIn('[S1]', result['answer'])
            self.assertEqual(result['sources'][0]['library_item_id'], item['id'])
            self.assertEqual(result['sources'][0]['quotes'], [body])
            unrelated = self.client.post(f"/library/{item['id']}/ask", json={'question': '量子色动力学'}).json()
            self.assertEqual(unrelated['sources'], [])
            self.assertEqual(run_question()['sources'][0]['library_item_id'], item['id'])
            self.assertEqual(self.client.delete('/library/' + item['id']).status_code, 204)
            self.assertEqual(run_question()['sources'], [])
            response = self.client.post('/tasks', json={**request, 'nonce': str(uuid4()), 'library_item_id': item['id']})
            self.assertEqual(response.status_code, 404)
            model.assert_not_called()

    def test_offline_overview_keeps_excerpts_beyond_article_intro(self):
        from .library_research import overview_quotes

        sections = ['Agent 的目标是根据工具返回的结果决定下一步，因此每次工具调用都需要有明确的输入和可核对的输出。',
                    'Agent 执行期间应保存观察结果，调试时可以用实际返回值对照每一次状态流转以及触发它的条件。',
                    'Agent 上线之前可以用一条固定资料做对照测试，同时验证阶段切换和来源链接是否指向原文。']
        body = '\n'.join(f'## 第 {index} 节\n{paragraph}' for index, paragraph in enumerate(sections))
        self.assertEqual(overview_quotes(body, 'Agent 工程'), sections)

    def test_single_reader_sends_current_body_and_checks_exact_model_quotes(self):
        from zhihu_agent.kimi import KimiError

        text = '精选阅读方案先找出处，再核对引用所在段落。'
        item = self.client.post('/library', json={'source': 'note', 'title': '精选阅读方案', 'body': text}).json()
        self.client.post('/library', json={'source': 'note', 'title': '另一份资料', 'body': '不可外送的另一份正文。'}).raise_for_status()
        path = f"/library/{item['id']}/ask"
        payload = {'question': '这篇回答什么意思？', 'mode': 'live'}
        config = ModelConfig('kimi', 'k3', 'test-model-key')
        with patch('backend.library_research.ModelClient') as model:
            self.assertEqual(self.client.post(path, json=payload).status_code, 403)
            model.assert_not_called()
        payload['consent'] = True
        output = {'answer': '作者建议先定位来源，再核对段落 [S1]。', 'quotes': [text]}
        with (patch('backend.library_research.ModelClient') as model,
              patch('backend.library.require_live_model', return_value=config)):
            model.return_value.chat.return_value = {'choices': [{'message': {'content': json.dumps(output)}}]}
            result = self.client.post(path, json=payload).raise_for_status().json()
            self.assertEqual(result['generation'], {'mode': 'live', 'provider': 'Kimi Coding', 'model': 'k3'})
            self.assertEqual(result['answer'], output['answer'])
            self.assertEqual(result['sources'][0]['quotes'], [text])
            self.assertFalse(result['citation_warning'])
            model.return_value.chat.assert_called_once()
            messages, tools = model.return_value.chat.call_args.args
            self.assertEqual(json.loads(messages[-1]['content'])['正文'], [text])
            self.assertEqual(tools, [])

            output['quotes'] = ['这是一条凭空编造的引文。']
            model.return_value.chat.return_value['choices'][0]['message']['content'] = json.dumps(output)
            result = self.client.post(path, json=payload).raise_for_status().json()
            self.assertEqual(result['sources'], [])
            self.assertTrue(result['citation_warning'])

            model.return_value.chat.side_effect = KimiError('simulated provider failure')
            self.assertEqual(self.client.post(path, json=payload).status_code, 502)

    def test_reader_distinguishes_current_answer_and_notes_personalize_the_next_batch(self):
        title = '如何看待文章《模型一代》？为什么它能传播得如此迅速和广泛？'
        url = 'https://www.zhihu.com/question/20/answer/30'
        body = ('作者认为 AI 带来的生产力变化给文科生提供了新机会，也要求人们重新思考技能和职位之间的关系。'
                '\n\n作者讨论了技术转型中的不确定性，建议把讨论重点放在真实职业变化。')
        with patch('backend.library.ZhihuGateway.run', return_value={'metadata': {'id': 30, 'title': title}, 'content_md': body}):
            preview = self.client.get('/library/preview', params={'url': url}).json()
        question_path = '/library/preview/ask'
        query = {'preview_id': preview['preview_id'], 'question': '为什么《模型一代》传播得这么快？'}
        result = self.client.post(question_path, json=query).json()
        self.assertEqual(result['sources'], [])
        self.assertIn('没有找到明确说明传播原因', result['answer'])
        overview = self.client.post(question_path, json={**query, 'question': f'「{title}」主要讲了什么？'}).json()
        self.assertIn('生产力变化', overview['answer'])
        self.assertIn('[S1]', overview['answer'])
        meaning = self.client.post(question_path, json={**query, 'question': '这篇回答什么意思'}).json()
        self.assertEqual(meaning['generation'], {'mode': 'local'})
        self.assertIn('生产力变化', meaning['answer'])
        self.assertIn('[S1]', meaning['answer'])
        self.assertEqual(self.client.post(question_path, json={**query, 'mode': 'live'}).status_code, 403)
        suggestions = self.client.get('/library/questions', params={'preview_id': preview['preview_id']}).json()['questions']
        self.assertIn('这篇知乎回答', suggestions[0]['text'])
        added = self.client.post('/library', json={
            'source': 'note', 'title': '随手笔记', 'url': url,
            'body': '## 回答\n\nSQLite 快照隔离与并发读写。\n\n## 来源\n\nOpenAPI 配置与代理反向转发。',
        }).json()
        self.assertEqual(added['url'], url)
        candidates = [{'id': '10', 'target_type': 'article', 'title': 'OpenAPI 配置与代理反向转发'},
                      {'id': '12', 'target_type': 'article', 'title': 'SQLite 快照隔离与并发读写'}]
        with patch('backend.discover.cli_read', return_value=candidates):
            result = self.client.get('/discover/articles?auto=true').json()
            self.assertEqual(result['items'][0]['candidate_id'], 'article:12')
            self.assertEqual(result['items'][0]['favorite_origin'], 'note')
            self.assertIsNone(result['items'][1]['favorite_title'])
            explicit = self.client.get('/discover/articles', params={'auto': True, 'goal': 'OpenAPI 配置'}).json()
            self.assertEqual(explicit['items'][0]['candidate_id'], 'article:10')
            self.client.delete('/library/' + added['id']).raise_for_status()
            self.assertTrue(all(item['favorite_origin'] != 'note'
                                for item in self.client.get('/discover/articles?auto=true').json()['items']))

    def test_fitness_pitch_without_selling_action_is_flagged_without_being_filtered(self):
        from .feed_filter import is_promotion, suspected_promotion

        article = {'title': '减脂总卡平台？压力胖的原因找不到，其实可能是晚上“没呼吸好”',
                   'excerpt': '最近在咨询中，大家重复问几个问题。'}
        self.assertTrue(suspected_promotion(article['title'], article['excerpt']))
        self.assertFalse(is_promotion(article))
        self.assertTrue(is_promotion({**article, 'content_html': '<p>加微信预约咨询，报名购买训练营。</p>'}))
        self.assertFalse(is_promotion({**article, 'content_html': '<p>警惕扫码加微信购买减脂课程的骗局。</p>'}))

    def test_locks_active_and_live_mode_requires_opt_in(self):
        self.assertEqual(self.client.post('/tasks', json={'nonce': str(uuid4()), 'focus': 'test',
                         'mode': 'live', 'consent': True}).status_code, 403)
        payload = {'nonce': str(uuid4()), 'focus': 'Agent'}
        self.assertEqual(self.client.post('/tasks', json=payload, headers={'x-zhihu-workbench': '0'}).status_code, 403)
        self.assertEqual(self.client.post('/tasks', json={**payload, 'consent': 'yes'}).status_code, 422)
        self.assertEqual(self.client.get('/health', headers={'host': 'other.example'}).status_code, 403)
        wait = Event()

        def blocked_demo(*args):
            wait.wait(3)
            return run_demo(*args)

        with patch('backend.main.run_demo', side_effect=blocked_demo):
            try:
                self.assertEqual(self.client.post('/tasks', json=payload).status_code, 202)
                self.assertEqual(self.client.post('/tasks', json={**payload, 'nonce': str(uuid4())}).status_code, 409)
            finally:
                wait.set()

    def test_research_uses_original_search_read_and_citations(self):
        request = {'nonce': str(uuid4()), 'focus': RESEARCH_QUESTION, 'kind': 'research'}
        response = self.client.post('/tasks', json=request)
        self.assertEqual(response.status_code, 202)
        task_id = response.json()['id']
        for _ in range(60):
            task = self.client.get(f'/tasks/{task_id}').json()
            if task['status'] not in {'queued', 'running'}:
                break
            time.sleep(.015)
        self.assertEqual(task['status'], 'completed', task)
        result = task['result']
        self.assertEqual(result['skill']['name'], 'source-grounded-research')
        self.assertEqual([row['tool'] for row in result['trace']], ['search_answers', 'read_answer'])
        self.assertIn('[S1]', result['answer'])
        self.assertEqual(result['sources'][0]['citation_id'], 'S1')
        self.assertFalse(result['citation_warning'])

    def test_metrics_recompute_with_unknowns_and_leave_proxy_independent(self):
        proxy = self.client.get('/lab/metrics?source=ai_proxy').json()
        self.assertEqual(proxy['labeled'], 20)
        self.assertEqual(proxy['cutoffs']['1']['metrics']['hit_at_k']['challenger_mean'], .5)
        self.assertEqual(self.client.get('/lab/metrics').json()['labeled'], 0)
        batch = self.client.get('/lab/cases').json()[2]
        url = '/lab/cases/' + batch['case_id']
        form = self.client.get(url).json()
        self.assertNotIn('rankings', form)
        grades = [{'candidate_id': row['candidate_id'], 'grade': None} for row in form['items']]
        grades[0]['grade'] = 2
        self.assertEqual(self.client.put(url + '/ratings', json={'items': grades}).json(), {'labeled': 1})
        self.assertEqual(self.client.put(url + '/ratings', json={'items': [grades[0]] * len(grades)}).status_code, 422)
        human = self.client.get('/lab/metrics').json()
        self.assertEqual(human['labeled'], 1)
        self.assertIsNone(human['cutoffs']['3']['metrics']['ndcg_at_k']['challenger_mean'])
        self.assertEqual(self.client.get('/lab/metrics?source=ai_proxy').json(), proxy)
        self.assertEqual(self.client.get(url).json()['items'][0]['grade'], 2)
        self.assertIn('rankings', self.client.get(url + '/comparison').json())

    def test_restart_marks_uncertain_work_interrupted(self):
        store = Store(self.db)
        task_id, _ = store.create(str(uuid4()), 'demo', 'Agent')
        store.start(task_id)
        store.init()
        store.finish(task_id, result={'late': True})
        self.assertFalse(store.start(task_id))
        self.assertEqual(store.task(task_id)['status'], 'interrupted')
        self.assertEqual(store.task(task_id)['events'][-1]['stage'], 'interrupted')

    def test_agent_rl_reports_held_out_rollouts_without_writing_user_data(self):
        from .agent_rl import TEST, TRAIN, allowed, rollout, take_step, train

        self.assertTrue({case.topic for case in TRAIN}.isdisjoint(case.topic for case in TEST))
        case = next(case for case in TEST if case.pattern == 'no_support')
        state = ('unread', 'unread')
        with self.assertRaises(ValueError):
            take_step(case, state, 'publish')
        state, _, _ = take_step(case, state, 'read_1')
        self.assertIn('verify_1', allowed(state))
        state, _, _ = take_step(case, state, 'verify_1')
        self.assertEqual(state[0], 'rejected')
        self.assertNotIn('publish', allowed(state))

        policy = train(2026, 3000)
        outcomes = [rollout(case, policy)[0] for case in TEST]
        data = self.client.get('/lab/agent-rl').json()
        learned = data['strategies'][-1]
        self.assertEqual(learned['published'], outcomes.count('published'))
        self.assertEqual(learned['correct_abstentions'], outcomes.count('abstained'))
        self.assertEqual(sum(learned[key] for key in ('published', 'correct_abstentions', 'missed')), len(TEST))
        self.assertEqual(self.client.get('/tasks').json(), [])
        self.assertEqual(Store(self.db).discovery_ratings(), {})


if __name__ == '__main__':
    unittest.main()
