"""Transactional business API tests against an isolated relational database."""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
import mains


CTX = {'tenant_id': 'A', 'plant_id': 'P', 'line_id': 'L', 'role': '工程师'}
PARAM = {'device_name': '测试设备', 'parameter': '速度', 'value': '20 m/min', 'tolerance': '±1'}
FAULT = {'device_type': '测试设备', 'code': 'T01', 'description': '报警', 'solution': '停机检查', 'severity': '中'}


class BusinessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        with self.engine.begin() as conn:
            for sql in [
                'CREATE TABLE plants (plant_id TEXT PRIMARY KEY, tenant_id TEXT)',
                'CREATE TABLE production_lines (line_id TEXT PRIMARY KEY, plant_id TEXT)',
                'CREATE TABLE device_parameters (id INTEGER PRIMARY KEY AUTOINCREMENT, line_id TEXT, device_name TEXT, parameter TEXT, value TEXT, tolerance TEXT, UNIQUE(line_id, device_name, parameter))',
                'CREATE TABLE fault_knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, device_type TEXT, code TEXT, description TEXT, solution TEXT, severity TEXT, UNIQUE(tenant_id, device_type, code))',
                "INSERT INTO plants VALUES ('P','A'),('Q','B')",
                "INSERT INTO production_lines VALUES ('L','P'),('M','Q')",
            ]:
                conn.execute(text(sql))
        self.state = type('State', (), {'engine': self.engine})()
        self.mock = patch.object(mains, 'init_state', return_value=self.state)
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.addCleanup(self.engine.dispose)
        self.client = TestClient(mains.app)

    def save(self, record=PARAM, ctx=CTX, kind='equipment', **kw):
        return self.client.post(f'/business/{kind}/save', json={'user_ctx': ctx, 'record': record, **kw})

    def browse(self, kind='equipment', ctx=CTX, **kw):
        return self.client.post(f'/business/{kind}/list', json={'user_ctx': ctx, **kw}).json()

    def test_create_edit_conflict_duplicate_and_rollback(self):
        self.assertEqual(self.save().status_code, 200)
        row = self.browse()['items'][0]
        self.assertEqual(self.save().status_code, 409)
        self.assertEqual(self.save({**PARAM, 'value': '30 m/min'}, id=row['id'], revision=row['revision']).status_code, 200)
        self.assertEqual(self.save(id=row['id'], revision=row['revision']).status_code, 409)
        self.assertEqual(self.browse()['items'][0]['value'], '30 m/min')
        self.save({**PARAM, 'parameter': '功率'})
        fresh = self.browse()['items'][0]
        other = '速度' if fresh['parameter'] == '功率' else '功率'
        self.assertEqual(self.save({**PARAM, 'parameter': other}, id=fresh['id'], revision=fresh['revision']).status_code, 409)
        self.assertEqual(self.browse()['total'], 2)

    def test_roles_scope_and_blank_fields(self):
        self.assertEqual(self.save(ctx={**CTX, 'role': '操作员'}).status_code, 403)
        self.assertEqual(self.save(ctx={**CTX, 'tenant_id': 'B'}).status_code, 403)
        self.assertEqual(self.save({**PARAM, 'value': '   '}).status_code, 422)
        self.save()
        foreign = {**CTX, 'tenant_id': 'B', 'plant_id': 'Q', 'line_id': 'M'}
        self.assertEqual(self.browse(ctx=foreign)['total'], 0)
        self.assertEqual(self.save(ctx=foreign, id=1, revision='bad').status_code, 404)
        self.assertEqual(self.browse()['items'][0]['value'], PARAM['value'])

    def test_faults_search_literal_wildcards_and_severity(self):
        self.assertEqual(self.save(FAULT, kind='faults').status_code, 200)
        self.assertEqual(self.browse('faults', keyword='停机', severity='中')['total'], 1)
        self.assertEqual(self.browse('faults', severity='高')['total'], 0)
        self.assertEqual(self.browse('faults', keyword="' OR 1=1 --")['total'], 0)
        self.assertEqual(self.browse('faults', keyword='%')['total'], 0)
        row = self.browse('faults')['items'][0]
        self.assertEqual(self.save({**FAULT, 'solution': '修订步骤'}, kind='faults', id=row['id'], revision=row['revision']).status_code, 200)
        self.assertEqual(self.browse('faults')['items'][0]['solution'], '修订步骤')

    def test_form_created_and_updated_fault_is_immediately_available_to_chat(self):
        from state_store import StateStore
        from workflow import ConversationRAG
        from rag import HybridRetriever
        from test_rag import Store
        from test_workflow import ScriptLLM, verified
        state = StateStore(self.engine, redis_url='')
        self.assertEqual(self.save(FAULT, kind='faults').status_code, 200)
        for expected in ('停机检查', '修订后的步骤'):
            if expected != '停机检查':
                row = self.browse('faults')['items'][0]
                self.assertEqual(self.save({**FAULT, 'solution': expected}, kind='faults',
                    id=row['id'], revision=row['revision']).status_code, 200)
            service = ConversationRAG(HybridRetriever(Store([])),
                ScriptLLM(expected + '。[S1]', verified()), state)
            with patch.object(mains, 'init_rag', return_value=service), patch.object(mains, 'init_state', return_value=state):
                response = self.client.post('/query', json={'user_ctx': CTX, 'question': 'T01如何处理'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(expected, response.json()['sources'][0]['text'])
            self.assertEqual(response.json()['sources'][0]['metadata']['doc_type'], 'fault')

    def test_pagination_has_full_count(self):
        for n in range(53):
            self.save({**PARAM, 'parameter': f'参数{n:03}'})
        first, last = self.browse(), self.browse(page=99)
        self.assertEqual(first['total'], 53)
        self.assertEqual(len(first['items']), 50)
        self.assertEqual(last['page'], 2)
        self.assertEqual(len(last['items']), 3)


if __name__ == '__main__':
    unittest.main()
