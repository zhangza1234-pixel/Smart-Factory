"""Authentication boundary tests through the real API, with an isolated database."""
import json
import time
import unittest
from unittest.mock import patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, select
from sqlalchemy.pool import StaticPool

import mains
from auth import AuthStore, NewAccount, schema, sessions, users
from state_store import StateStore

PASSWORD = 'test-password-123'


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool,
                                    connect_args={'check_same_thread': False})
        self.state = StateStore(self.engine, redis_url='')
        schema.create_all(self.engine)
        with self.engine.begin() as conn:
            for sql in [
                'CREATE TABLE plants (plant_id TEXT PRIMARY KEY, tenant_id TEXT, name TEXT)',
                'CREATE TABLE production_lines (line_id TEXT PRIMARY KEY, plant_id TEXT, name TEXT)',
                'CREATE TABLE device_parameters (id INTEGER PRIMARY KEY, line_id TEXT, device_name TEXT, parameter TEXT, value TEXT, tolerance TEXT)',
                'CREATE TABLE fault_knowledge (id INTEGER PRIMARY KEY, tenant_id TEXT, device_type TEXT, code TEXT, description TEXT, solution TEXT, severity TEXT)',
                "INSERT INTO plants VALUES ('P','A','工厂A'),('Q','B','工厂B')",
                "INSERT INTO production_lines VALUES ('L','P','产线A'),('M','Q','产线B')",
                "INSERT INTO device_parameters VALUES (1,'L','设备A','速度','1',''),(2,'M','设备B','速度','2','')",
                "INSERT INTO fault_knowledge VALUES (1,'A','设备A','FA','A现象','A方案','低'),(2,'B','设备B','FB','B现象','B方案','高')",
            ]:
                conn.execute(text(sql))
        self.store = AuthStore(self.engine)
        self.expert = self.create('expert_a', 'A', 'P', 'L', '专家')
        self.operator = self.create('operator_a', 'A', 'P', 'L', '操作员')
        self.other = self.create('expert_b', 'B', 'Q', 'M', '专家')
        patcher = patch.object(mains, 'init_state', return_value=self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.engine.dispose)
        self.client = TestClient(mains.app)
        self.login = self.sign_in('expert_a')
        self.client.headers['Authorization'] = 'Bearer ' + self.login['access_token']
        self.ctx = self.login['user_ctx']

    def create(self, username, tenant, plant, line, role):
        return self.store.create_account(tenant, NewAccount(username=username, password=PASSWORD,
            plant_id=plant, line_id=line, role=role))

    def sign_in(self, username, password=PASSWORD):
        response = self.client.post('/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def post_ctx(self, path, ctx=None, **payload):
        return self.client.post(path, json={'user_ctx': self.ctx if ctx is None else ctx, **payload})

    def test_every_application_route_requires_login(self):
        self.client.headers.clear()
        for route in mains.app.routes:
            if not isinstance(route, APIRoute) or route.path in ('/health', '/auth/login'):
                continue
            path = route.path
            for name in ('user_id', 'document_id', 'session_id', 'memory_id', 'kind'):
                path = path.replace('{' + name + '}', 'equipment' if name == 'kind' else 'test')
            for method in route.methods:
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path, json={})
                    self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_password_validation_and_lockout(self):
        for name in ('missing', 'expert_a'):
            self.assertEqual(self.client.post('/auth/login', json={'username': name, 'password': 'bad'}).status_code, 401)
        for _ in range(4):
            self.client.post('/auth/login', json={'username': 'expert_a', 'password': 'bad'})
        self.assertEqual(self.client.post('/auth/login', json={'username': 'expert_a', 'password': PASSWORD}).status_code, 401)
        with self.engine.begin() as conn:
            conn.execute(users.update().where(users.c.id == self.expert['user_id']).values(locked_until=0))
        self.sign_in('expert_a')
        with self.engine.connect() as conn:
            row = conn.execute(select(users).where(users.c.id == self.expert['user_id'])).mappings().one()
        self.assertNotIn(PASSWORD, row['password_hash'])
        self.assertEqual(row['failed_attempts'], 0)

    def test_identity_and_scope_tampering_are_rejected_before_data_access(self):
        for key, value in [('tenant_id', 'B'), ('plant_id', 'Q'), ('line_id', 'M'),
                           ('role', '操作员'), ('user_id', self.operator['user_id'])]:
            with self.subTest(field=key):
                forged = {**self.ctx, key: value}
                self.assertEqual(self.post_ctx('/query', forged, question='你好').status_code, 403)
                self.assertEqual(self.post_ctx('/business/faults/list', forged).status_code, 403)
                response = self.client.post('/upload-document', data={'user_ctx': json.dumps(forged),
                    'category': '标准', 'access_level': '操作员'}, files={'file': ('a.txt', b'test')})
                self.assertEqual(response.status_code, 403)

    def test_business_and_account_lists_are_tenant_scoped(self):
        for kind in ('equipment', 'faults'):
            result = self.post_ctx(f'/business/{kind}/list')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual([r['id'] for r in result.json()['items']], [1])
        rows = self.client.get('/auth/accounts').json()['accounts']
        self.assertEqual({row['username'] for row in rows}, {'expert_a', 'operator_a'})
        self.assertNotIn('password_hash', json.dumps(rows))
        self.assertNotIn('expert_b', json.dumps(rows))
        scopes = self.client.get('/auth/scopes').json()['scopes']
        self.assertEqual([r['line_id'] for r in scopes], ['L'])
        self.assertEqual(self.client.post('/auth/accounts/' + self.other['user_id'], json={'active': False}).status_code, 404)

    def test_expert_provisions_only_valid_scopes_in_own_tenant(self):
        account = {'username': 'new_user', 'password': PASSWORD, 'role': '工程师', 'plant_id': 'P', 'line_id': 'L'}
        self.assertEqual(self.client.post('/auth/accounts', json={**account, 'plant_id': 'Q', 'line_id': 'M'}).status_code, 422)
        self.assertEqual(self.client.post('/auth/accounts', json={**account, 'tenant_id': 'B'}).status_code, 422)
        response = self.client.post('/auth/accounts', json=account)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['tenant_id'], 'A')
        self.assertEqual(self.client.post('/auth/accounts', json=account).status_code, 409)
        self.assertEqual(self.client.post('/auth/accounts', json={**account, 'password': 'short'}).status_code, 422)
        self.assertEqual(self.sign_in('new_user')['user_ctx']['role'], '工程师')

    def test_operator_cannot_manage_accounts_or_escalate_privileges(self):
        operator_login = self.sign_in('operator_a')
        self.client.headers['Authorization'] = 'Bearer ' + operator_login['access_token']
        ctx = operator_login['user_ctx']
        self.assertEqual(self.client.get('/auth/accounts').status_code, 403)
        self.assertEqual(self.client.get('/auth/scopes').status_code, 403)
        self.assertEqual(self.client.get('/db-status').status_code, 403)
        self.assertEqual(self.post_ctx('/documents/test/status', ctx, status='published').status_code, 403)
        record = {'device_name': 'a', 'parameter': 'b', 'value': 'c'}
        self.assertEqual(self.post_ctx('/business/equipment/save', ctx, record=record).status_code, 403)
        self.assertEqual(self.post_ctx('/business/equipment/save', {**ctx, 'role': '专家'}, record=record).status_code, 403)
        self.assertEqual(self.client.post('/auth/accounts/' + self.expert['user_id'], json={'active': False}).status_code, 403)

    def test_logout_expiry_and_invalid_tokens(self):
        token = self.login['access_token']
        with self.engine.connect() as conn:
            row = conn.execute(select(sessions)).mappings().first()
            self.assertNotEqual(row['token_hash'], token)
        self.assertEqual(self.client.post('/auth/logout').status_code, 200)
        self.assertEqual(self.client.get('/auth/me').status_code, 401)
        login = self.sign_in('expert_a')
        self.client.headers['Authorization'] = 'Bearer ' + login['access_token']
        with self.engine.begin() as conn:
            conn.execute(sessions.update().values(expires_at=time.time() - 1))
        self.assertEqual(self.client.get('/auth/me').status_code, 401)
        self.client.headers['Authorization'] = 'Bearer forged-token'
        self.assertEqual(self.client.get('/auth/me').status_code, 401)

    def test_disable_and_password_reset_revoke_all_sessions(self):
        first, second = self.sign_in('operator_a'), self.sign_in('operator_a')
        path = '/auth/accounts/' + self.operator['user_id']
        self.assertEqual(self.client.post(path, json={'active': False}).status_code, 200)
        for login in (first, second):
            self.assertEqual(self.client.get('/auth/me', headers={'Authorization': 'Bearer ' + login['access_token']}).status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={'username': 'operator_a', 'password': PASSWORD}).status_code, 401)
        self.assertEqual(self.client.post(path, json={'active': True}).status_code, 200)
        login = self.sign_in('operator_a')
        self.assertEqual(self.client.post(path, json={'password': 'new-password-456'}).status_code, 200)
        self.assertEqual(self.client.get('/auth/me', headers={'Authorization': 'Bearer ' + login['access_token']}).status_code, 401)
        self.assertEqual(self.client.post('/auth/login', json={'username': 'operator_a', 'password': PASSWORD}).status_code, 401)
        self.sign_in('operator_a', 'new-password-456')
        self.assertEqual(self.client.post('/auth/accounts/' + self.expert['user_id'], json={'active': False}).status_code, 409)

    def test_conversations_and_memories_are_isolated_between_accounts(self):
        first = self.post_ctx('/query', question='你好')
        self.assertEqual(first.status_code, 200, first.text)
        session_id = first.json()['session_id']
        memory = self.post_ctx('/memories', content='我的偏好', kind='preference')
        self.assertEqual(memory.status_code, 200, memory.text)
        operator_login = self.sign_in('operator_a')
        self.client.headers['Authorization'] = 'Bearer ' + operator_login['access_token']
        ctx = operator_login['user_ctx']
        response = self.post_ctx('/sessions/' + session_id + '/history', ctx)
        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(self.post_ctx('/memories/list', ctx).json()['memories'], [])
        self.assertIn(self.post_ctx('/memories/' + memory.json()['id'] + '/delete', ctx).status_code, (403, 404))


if __name__ == '__main__':
    unittest.main()
