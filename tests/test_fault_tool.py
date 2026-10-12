"""Real relational fault lookup with tenant isolation and conversational orchestration."""
from datetime import date
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from sqlalchemy import text

from rag import HybridRetriever
from workflow import ConversationRAG
from tests.test_workflow import StateFixture, ScriptLLM, plan, verified
from tests.test_rag import Store


def fault_step(**filters):
    return {'question': '机器人 RB1001 如何处理', 'tool': 'faults', 'depends_on': [],
            'fault_code': 'RB1001', **filters}


class FaultToolTests(StateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # These tests pin model plans to exercise each tool branch, including dependent hops.
        self.routing_patch = patch('workflow.direct_fault_plan', return_value=None)
        self.routing_patch.start()
        self.addCleanup(self.routing_patch.stop)
        with self.engine.begin() as conn:
            conn.execute(text('CREATE TABLE fault_knowledge (id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT, device_type TEXT, code TEXT, description TEXT, solution TEXT, severity TEXT)'))
            conn.execute(text('INSERT INTO fault_knowledge (tenant_id,device_type,code,description,solution,severity) VALUES (:tenant,:device,:code,:description,:solution,:severity)'), [
                dict(tenant='A', device='机器人', code='RB1001', description='坐标版本不一致', solution='核对批准版本', severity='高'),
                dict(tenant='B', device='其他租户设备', code='RB1001', description='机密现象', solution='机密处理', severity='高'),
                dict(tenant='A', device='PLC控制柜', code='PLC01', description='通信中断', solution='检查日志', severity='中'),
            ])

    def service(self, llm):
        return ConversationRAG(HybridRetriever(Store(self.records)), llm, self.state)

    def test_sql_only_answer_has_citation_and_no_vector_call(self):
        llm = ScriptLLM(plan('RB1001如何处理', [fault_step()]), 'RB1001应核对批准版本。[S1]', verified())
        service = self.service(llm)
        with patch.object(service.retriever, 'retrieve') as retrieve:
            result = service.query('RB1001如何处理', self.ctx)
            retrieve.assert_not_called()
        self.assertTrue(result['citation_valid'], result)
        self.assertEqual(result['confidence']['level'], 'sufficient')
        self.assertEqual(result['sources'][0]['metadata']['doc_type'], 'fault')
        self.assertEqual(result['source_data'][0]['tool'], 'faults')
        self.assertNotIn('机密', json.dumps(result, ensure_ascii=False))

    def test_simple_code_and_short_followup_route_without_model_history(self):
        self.routing_patch.stop()
        llm = ScriptLLM('核对批准版本。[S1]', verified(), '严重程度为高。[S1]', verified())
        service = self.service(llm)
        first = service.query('RB1001是什么故障，如何处理？', self.ctx)
        second = service.query('那严重程度是什么？', self.ctx, session_id=first['session_id'])
        self.assertTrue(first['citation_valid'])
        self.assertTrue(second['citation_valid'])
        self.assertEqual(len(llm.inputs), 4)  # Answer and evidence check only, no planning call.
        self.assertEqual(second['retrieval_steps'][0]['tool'], 'faults')
        for payload in llm.inputs:
            self.assertNotIn('recent_messages', payload[1][1])

    def test_direct_router_leaves_mixed_tasks_and_new_topic_to_planner(self):
        self.routing_patch.stop()
        from workflow import direct_fault_plan
        history = {'messages': [{'role': 'user', 'content': 'RB1001如何处理'}]}
        self.assertIsNone(direct_fault_plan('我刚刚问的什么', history))
        self.assertIsNone(direct_fault_plan('RB1001对应哪份操作规范', history))
        self.assertIsNone(direct_fault_plan('机器人RB1001如何处理', history))
        self.assertIsNone(direct_fault_plan('你好', history))
        self.assertEqual(direct_fault_plan('UI-DEMO-01怎么处理', history)['steps'][0]['fault_code'], 'UI-DEMO-01')
        history['messages'].append({'role': 'user', 'content': 'PLC控制柜通信中断'})
        self.assertIsNone(direct_fault_plan('那严重程度是什么', history))

    def test_exact_code_tenant_scope_and_bound_literal_filters(self):
        self.assertEqual(len(self.state.faults(self.ctx, fault_code='rb1001')), 1)
        self.assertEqual(self.state.faults(self.ctx, fault_code='RB100'), [])
        self.assertEqual(self.state.faults(self.ctx, fault_code="' OR 1=1 --"), [])
        self.assertEqual(self.state.faults(self.ctx, device_type='%'), [])
        self.assertEqual(self.state.faults(self.ctx, keyword='_'), [])
        self.assertEqual(len(self.state.faults(self.ctx, device_type='PLC', keyword='通信中断')), 1)
        other = SimpleNamespace(**{**vars(self.ctx), 'tenant_id': 'B'})
        self.assertEqual(self.state.faults(other, fault_code='RB1001')[0]['solution'], '机密处理')
        with self.assertRaises(ValueError):
            self.state.faults(self.ctx)

    def test_current_snapshot_cannot_answer_historical_question(self):
        service = self.service(ScriptLLM(plan('历史RB1001', [fault_step()])))
        with patch.object(self.state, 'faults') as sql, patch.object(service.retriever, 'retrieve') as retrieve:
            result = service.query('2025年RB1001怎么处理', self.ctx)
            sql.assert_not_called()
            retrieve.assert_not_called()
        self.assertIn('当前快照', result['answer'])
        self.assertEqual(result['sources'], [])

    def test_duplicate_code_requires_device_clarification(self):
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO fault_knowledge (tenant_id,device_type,code,description,solution,severity) VALUES ('A','焊接机','RB1001','另一个现象','另一个处理','低')"))
        result = self.service(ScriptLLM(plan('RB1001', [fault_step()]))).query('RB1001怎么处理', self.ctx)
        self.assertIn('指定设备', result['answer'])
        self.assertEqual(result['sources'], [])
        self.assertEqual(len(self.state.faults(self.ctx, fault_code='RB1001', device_type='机器人')), 1)

    def test_missing_sql_record_falls_back_to_document_with_real_provenance(self):
        self.document('manual', text='ZZ999故障的处理为核对日志', start=date.today().isoformat())
        llm = ScriptLLM(plan('ZZ999故障处理', [fault_step(question='ZZ999故障处理', fault_code='ZZ999')]),
                        'ZZ999应核对日志。[S1]', verified())
        result = self.service(llm).query('ZZ999故障处理', self.ctx)
        self.assertTrue(result['citation_valid'], result)
        self.assertEqual(result['retrieval_steps'][0]['fallback_tool'], 'documents')
        self.assertEqual(result['source_data'][0]['tool'], 'chroma_search')

    def test_no_sql_or_document_evidence_never_generates_answer(self):
        llm = ScriptLLM(plan('ZZ999故障处理', [fault_step(fault_code='ZZ999')]))
        result = self.service(llm).query('ZZ999故障处理', self.ctx)
        self.assertEqual(len(llm.inputs), 1)
        self.assertIn('无匹配记录', result['answer'])
        self.assertEqual(result['sources'], [])

    def test_fault_followup_requeries_sql_and_uses_current_record(self):
        first = self.service(ScriptLLM(plan('RB1001', [fault_step()]), '核对批准版本。[S1]', verified())).query('RB1001怎么处理', self.ctx)
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE fault_knowledge SET solution='修订后的步骤' WHERE tenant_id='A' AND code='RB1001'"))
        llm = ScriptLLM(plan('RB1001处理步骤', [fault_step()]), '修订后的步骤。[S1]', verified())
        result = self.service(llm).query('那怎么处理', self.ctx, session_id=first['session_id'])
        self.assertIn('修订后的步骤', result['sources'][0]['text'])
        self.assertTrue(result['citation_valid'])
        self.assertIn('RB1001', json.loads(llm.inputs[0][1][1])['recent_messages'][0]['content'])

    def test_fault_then_document_dependent_lookup(self):
        self.document('manual', text='机器人 RB1001 的批准版本核对规范为规范A', start=date.today().isoformat())
        steps = [fault_step(), {'question': '故障处理对应操作规范', 'tool': 'documents', 'depends_on': [1]}]
        llm = ScriptLLM(plan('RB1001处理及对应规范', steps), {'query': '机器人 RB1001 批准版本核对规范', 'missing': False},
            '核对批准版本。[S1] 对应规范A。[S2]', verified(['S1', 'S2']))
        result = self.service(llm).query('RB1001处理及对应规范', self.ctx)
        self.assertTrue(result['citation_valid'], result)
        self.assertEqual([item['tool'] for item in result['retrieval_steps']], ['faults', 'documents'])

    def test_dependent_fault_filters_are_resolved_from_evidence(self):
        self.document('manual', text='机器人报警代码RB1001', start=date.today().isoformat())
        steps = [{'question': '机器人报警代码', 'tool': 'documents', 'depends_on': []},
                 fault_step(question='查询前置代码对应处理方法', fault_code='', depends_on=[1])]
        llm = ScriptLLM(plan('机器人报警代码对应处理', steps),
            {'query': '机器人RB1001处理', 'missing': False, 'fault_code': 'RB1001', 'device_type': '机器人', 'keyword': ''},
            '报警代码RB1001。[S1] 核对批准版本。[S2]', verified(['S1', 'S2']))
        result = self.service(llm).query('机器人报警代码对应处理', self.ctx)
        self.assertTrue(result['citation_valid'], result)
        self.assertEqual(result['sources'][1]['metadata']['doc_type'], 'fault')

    def test_overly_broad_fault_list_requires_narrower_conditions(self):
        with self.engine.begin() as conn:
            for n in range(12):
                conn.execute(text("INSERT INTO fault_knowledge (tenant_id,device_type,code,description,solution,severity) VALUES ('A','测试设备',:code,'测试','处理','低')"), {'code': str(n)})
        result = self.service(ScriptLLM(plan('测试设备故障', [fault_step(fault_code='', device_type='测试设备')]))).query('测试设备有哪些故障', self.ctx)
        self.assertIn('超过10条', result['answer'])
        self.assertEqual(result['sources'], [])


if __name__ == '__main__':
    unittest.main()
