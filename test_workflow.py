"""Behavioral regressions for temporal RAG, dependent hops and durable memory."""
from datetime import date
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from rag import HybridRetriever
from state_store import StateStore, family_identity, sessions, messages
from workflow import ConversationRAG, query_dates
from test_rag import Store, record


def ctx(**values):
    return SimpleNamespace(tenant_id='A', plant_id='P', line_id='L', role='专家', user_id='alice', **values)


def plan(question='压力上限', steps=None):
    return {'intent': 'retrieval', 'standalone_question': question, 'needs_clarification': False,
            'steps': steps or [{'question': question, 'depends_on': [], 'tool': 'documents'}]}


def verified(citations=None):
    return {'supported': True, 'question_covered': True, 'conflict': False, 'missing': [],
            'claims': [{'claim': '压力结论', 'citations': citations or ['S1'], 'supported': True}]}


class ScriptLLM:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.inputs = []

    def invoke(self, payload):
        self.inputs.append(payload)
        if not self.outputs:
            raise AssertionError('Unexpected model invocation')
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(content=output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))


class Cache:
    def __init__(self):
        self.values, self.ttls, self.broken = {}, [], False

    def get(self, key):
        if self.broken:
            raise ConnectionError('redis down')
        return self.values.get(key)

    def setex(self, key, ttl, value):
        if self.broken:
            raise ConnectionError('redis down')
        self.values[key] = value
        self.ttls.append(ttl)

    def delete(self, key):
        if self.broken:
            raise ConnectionError('redis down')
        self.values.pop(key, None)


class StateFixture:
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        self.addCleanup(self.engine.dispose)
        self.state = StateStore(self.engine, redis_url='')
        self.ctx = ctx()
        self.records = []

    def document(self, uid, version='2026', start='2026-01-01', end='', family='压力标准',
                 published=True, access='操作员', text='压力上限为8MPa'):
        metadata = {'tenant_id': 'A', 'plant_id': 'P', 'line_id': 'L', 'access_level': access,
            'family_id': family_identity(self.ctx, family), 'family_name': family, 'version': version,
            'title': family, 'effective_from': start, 'effective_to': end, 'original_filename': family + '.md'}
        self.state.stage(uid, self.ctx, metadata, 1)
        if published:
            self.state.change_status(uid, self.ctx, 'published')
        self.records.append(record(uid + ':0', text, **metadata, document_id=uid))
        return uid


class StateTests(StateFixture, unittest.TestCase):
    def test_current_historical_future_and_draft_versions(self):
        self.document('old', version='2025', start='2025-01-01', text='压力上限10MPa')
        self.document('new')
        self.document('future', version='2027', start='2027-01-01')
        self.document('draft', version='2028', start='2028-01-01', published=False)
        self.assertEqual(set(self.state.selected_versions(self.ctx, date(2025, 8, 1))), {'old'})
        self.assertEqual(set(self.state.selected_versions(self.ctx, date(2026, 8, 1))), {'new'})
        self.assertEqual(set(self.state.selected_versions(self.ctx, date(2028, 8, 1))), {'future'})
        self.assertEqual(next(row for row in self.state.catalog(self.ctx) if row['id'] == 'new')['supersedes'], 'old')

    def test_expiry_and_withdrawal_never_revive_old_standard(self):
        self.document('old', version='2025', start='2025-01-01')
        self.document('new', end='2026-06-01')
        self.assertEqual(self.state.selected_versions(self.ctx, date(2026, 6, 1)), {})
        self.state.change_status('new', self.ctx, 'withdrawn')
        self.assertEqual(self.state.selected_versions(self.ctx, date(2026, 2, 1)), {})

    def test_restricted_successor_does_not_leak_or_fallback(self):
        self.document('old', version='2025', start='2025-01-01')
        self.document('new', access='专家')
        operator = SimpleNamespace(**{**vars(self.ctx), 'role': '操作员'})
        self.assertEqual(self.state.selected_versions(operator, date(2026, 3, 1)), {})

    def test_duplicate_version_and_effective_date_conflicts(self):
        self.document('first')
        with self.assertRaises(HTTPException) as error:
            self.document('different')
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            self.document('same_date', version='revision2')
        self.state.change_status('first', self.ctx, 'withdrawn')
        self.state.change_status('same_date', self.ctx, 'published')
        self.assertEqual(set(self.state.selected_versions(self.ctx, date(2026, 1, 2))), {'same_date'})

    def test_temporal_filter_runs_before_bm25_and_vector_ranking(self):
        self.document('old', version='2025', start='2025-01-01', text='压力压力压力上限10MPa')
        self.document('new', text='压力上限8MPa')
        store = Store(self.records, [(self.records[0], 0.001), (self.records[1], 0.2)])
        retriever = HybridRetriever(store)
        selected = self.state.selected_versions(self.ctx, date(2026, 5, 1))
        result, _ = retriever.retrieve('压力上限', self.ctx, eligible=selected)
        self.assertEqual([item['metadata']['document_id'] for item in result], ['new'])
        self.assertEqual(store.where['$and'][-1], {'document_id': {'$in': ['new']}})
        self.assertEqual(retriever.retrieve('压力', self.ctx, eligible={})[0], [])

    def test_session_ownership_concurrency_restart_and_clear(self):
        empty = {'answer': '已回答', 'sources': []}
        with self.state.conversation(self.ctx) as (sid, token, history):
            self.assertEqual(history['messages'], [])
            with self.assertRaises(HTTPException) as busy:
                with self.state.conversation(self.ctx, sid):
                    pass
            self.assertEqual(busy.exception.status_code, 409)
            self.state.save_turn(self.ctx, sid, token, '问题', empty)
        restarted = StateStore(self.engine, redis_url='')
        self.assertEqual(len(restarted.history(self.ctx, sid)['messages']), 2)
        for change in ({'user_id': 'bob'}, {'tenant_id': 'B'}, {'role': '工程师'}, {'line_id': 'other'}):
            with self.assertRaises(HTTPException):
                restarted.history(SimpleNamespace(**{**vars(self.ctx), **change}), sid)
        restarted.clear_session(self.ctx, sid)
        self.assertEqual(restarted.history(self.ctx, sid)['messages'], [])

    def test_redis_stale_cache_outage_and_recovery(self):
        cache = Cache()
        self.state.redis = cache
        with self.state.conversation(self.ctx) as (sid, token, _):
            stale = dict(cache.values)
            cache.broken = True
            self.state.save_turn(self.ctx, sid, token, '问题', {'answer': '回答', 'sources': []})
        self.assertEqual(len(self.state.history(self.ctx, sid)['messages']), 2)
        self.assertEqual(self.state.cache_status, 'unavailable_mysql_fallback')
        cache.broken = False
        cache.values = stale
        self.assertEqual(len(self.state.history(self.ctx, sid)['messages']), 2)
        self.assertTrue(all(ttl >= 60 for ttl in cache.ttls))
        self.state.clear_session(self.ctx, sid)
        self.assertEqual(cache.values, {})

    def test_long_memory_explicit_ownership_source_and_delete(self):
        memory = self.state.save_memory(self.ctx, 'preference', '请简短回答')
        self.assertEqual(len(self.state.list_memories(self.ctx)), 1)
        bob = SimpleNamespace(**{**vars(self.ctx), 'user_id': 'bob'})
        self.assertEqual(self.state.list_memories(bob), [])
        with self.assertRaises(HTTPException):
            self.state.delete_memory(bob, memory['id'])
        with self.assertRaises(HTTPException):
            self.state.save_memory(self.ctx, 'background', '错误来源', source_id='missing')
        self.state.delete_memory(self.ctx, memory['id'])
        self.assertEqual(self.state.list_memories(self.ctx), [])


class WorkflowTests(StateFixture, unittest.TestCase):
    def service(self, llm):
        return ConversationRAG(HybridRetriever(Store(self.records)), llm, self.state)

    def test_greeting_and_history_never_retrieve_or_inherit_old_fault(self):
        for question in ('你好', '我刚刚问的什么'):
            with self.subTest(question=question):
                with self.state.conversation(self.ctx) as (sid, token, _):
                    self.state.save_turn(self.ctx, sid, token, 'RB1001是什么故障？',
                        {'answer': '旧故障回答', 'sources': []})
                llm = ScriptLLM(plan('RB1001是什么故障'))
                service = self.service(llm)
                with patch.object(service.retriever, 'retrieve', return_value=([], {})) as retrieve:
                    result = service.query(question, self.ctx, session_id=sid)
                    retrieve.assert_not_called()
                self.assertEqual(llm.inputs, [])
                self.assertEqual(result['sources'], [])
                self.assertEqual(result['retrieval_steps'], [])
                if question == '你好':
                    self.assertIn('你好', result['answer'])
                    self.assertNotIn('RB1001', result['answer'])
                else:
                    self.assertIn('RB1001是什么故障？', result['answer'])
                    self.assertNotIn('旧故障回答', result['answer'])

    def test_answer_verifies_support_and_explains_confidence(self):
        self.document('new')
        llm = ScriptLLM(plan(), '压力上限为8MPa。[S1]', verified())
        result = self.service(llm).query('压力上限', self.ctx, as_of=date(2026, 5, 1))
        self.assertTrue(result['citation_valid'])
        self.assertEqual(result['confidence']['level'], 'sufficient')
        self.assertEqual(result['sources'][0]['metadata']['version'], '2026')

    def test_general_chat_route_does_not_load_vectors(self):
        from unittest.mock import Mock
        factory = Mock(side_effect=AssertionError('Must not load vectors for chat'))
        retriever = HybridRetriever(None, store_factory=factory)
        llm = ScriptLLM({'intent': 'chat', 'answer': '当然，可以聊一聊。', 'steps': []})
        result = ConversationRAG(retriever, llm, self.state).query('我们换个轻松的话题吧', self.ctx)
        self.assertEqual(result['intent'], 'chat')
        self.assertEqual(result['sources'], [])
        factory.assert_not_called()

    def test_invalid_intent_plan_does_not_force_retrieval(self):
        for output in ('bad JSON', {'intent': 'unknown'}, {'steps': []}):
            with self.subTest(output=output):
                service = self.service(ScriptLLM(output))
                with patch.object(service.retriever, 'retrieve') as retrieve:
                    result = service.query('换个话题吧', self.ctx)
                    retrieve.assert_not_called()
                self.assertEqual(result['intent'], 'clarification')
                self.assertEqual(result['confidence'], {})

    def test_model_history_route_uses_transcript_not_model_answer(self):
        with self.state.conversation(self.ctx) as (sid, token, _):
            self.state.save_turn(self.ctx, sid, token, 'RB1001是什么故障？', {'answer': '旧回答', 'sources': []})
        llm = ScriptLLM({'intent': 'history', 'answer': '虚构的问题', 'steps': []})
        result = self.service(llm).query('能帮我找一下刚才那条提问吗', self.ctx, session_id=sid)
        self.assertIn('RB1001是什么故障？', result['answer'])
        self.assertNotIn('虚构', result['answer'])
        self.assertEqual(result['retrieval_steps'], [])

    def test_greeting_with_technical_request_is_not_swallowed(self):
        self.document('new')
        result = self.service(ScriptLLM(plan(), '压力上限为8MPa。[S1]', verified())).query(
            '你好，请查压力上限', self.ctx, as_of=date(2026, 5, 1))
        self.assertEqual(result['intent'], 'retrieval')
        self.assertTrue(result['retrieval_steps'])

    def test_empty_history_and_other_user_remain_isolated(self):
        result = self.service(ScriptLLM()).query('我刚刚问的什么', self.ctx)
        self.assertIn('还没有', result['answer'])
        bob = SimpleNamespace(**{**vars(self.ctx), 'user_id': 'bob'})
        with self.assertRaises(HTTPException) as error:
            self.service(ScriptLLM()).query('我刚刚问的什么', bob, session_id=result['session_id'])
        self.assertEqual(error.exception.status_code, 404)

    def test_invalid_support_or_citation_never_passes(self):
        self.document('new')
        for check, answer in [(verified(), '压力上限8MPa。[S99]'),
                              ({**verified(), 'supported': False}, '压力上限10MPa。[S1]'),
                              ({**verified(), 'conflict': True}, '压力上限8MPa。[S1]'),
                              (ValueError('invalid JSON'), '压力上限8MPa。[S1]')]:
            with self.subTest(answer=answer, check=str(check)):
                result = self.service(ScriptLLM(plan(), answer, check)).query('压力上限', self.ctx, as_of=date(2026, 5, 1))
                self.assertFalse(result['citation_valid'])
                self.assertEqual(result['confidence']['level'], 'limited')

    def test_comparison_preserves_two_dates_and_requires_both_citations(self):
        self.document('old', version='2025', start='2025-01-01', text='压力上限10MPa')
        self.document('new')
        dates = [date(2025, 12, 31), date(2026, 5, 1)]
        result = self.service(ScriptLLM(plan(), '旧版10MPa。[S1] 新版8MPa。[S2]', verified(['S1', 'S2']))).query(
            '比较压力上限', self.ctx, comparison_dates=dates)
        self.assertTrue(result['citation_valid'])
        self.assertEqual([item['metadata']['document_id'] for item in result['sources']], ['old', 'new'])
        bad = self.service(ScriptLLM(plan(), '新版8MPa。[S2]', verified(['S2']))).query(
            '比较压力上限', self.ctx, comparison_dates=dates)
        self.assertEqual(bad['confidence']['level'], 'limited')

    def test_dependent_hop_uses_first_evidence_to_resolve_second_search(self):
        self.document('device', family='设备A手册', text='设备A使用标准 STD-2026')
        self.document('standard', family='STD-2026', text='STD-2026 压力上限8MPa')
        steps = [{'question': '设备A', 'depends_on': [], 'tool': 'documents'},
                 {'question': '查适用标准压力上限', 'depends_on': [1], 'tool': 'documents'}]
        llm = ScriptLLM(plan('设备A允许压力', steps), {'query': 'STD-2026 压力上限', 'missing': False},
                       '设备A适用STD-2026。[S1] 压力上限8MPa。[S2]', verified(['S1', 'S2']))
        result = self.service(llm).query('设备A允许压力', self.ctx, as_of=date(2026, 5, 1))
        self.assertTrue(result['citation_valid'], result)
        self.assertIn('STD-2026', result['retrieval_steps'][1]['query'])
        resolution_input = json.loads(llm.inputs[1][1][1])
        self.assertIn('STD-2026', resolution_input['evidence'][0]['text'])

    def test_missing_dependency_stops_generation(self):
        steps = [{'question': '不存在设备', 'depends_on': [], 'tool': 'documents'},
                 {'question': '适用标准', 'depends_on': [1], 'tool': 'documents'}]
        llm = ScriptLLM(plan('设备问题', steps))
        result = self.service(llm).query('设备问题', self.ctx)
        self.assertEqual(len(llm.inputs), 1)
        self.assertEqual(result['confidence']['level'], 'insufficient')
        self.assertEqual(result['retrieval_steps'][1]['missing'], '前置步骤缺少依据')

    def test_followup_loads_history_and_resolves_pronoun(self):
        self.document('new')
        service = self.service(ScriptLLM(plan(), '压力8MPa。[S1]', verified()))
        first = service.query('压力上限', self.ctx, as_of=date(2026, 5, 1))
        llm = ScriptLLM(plan('压力标准的压力上限'), '压力8MPa。[S1]', verified())
        second = self.service(llm).query('它呢', self.ctx, session_id=first['session_id'], as_of=date(2026, 5, 1))
        payload = json.loads(llm.inputs[0][1][1])
        self.assertEqual(payload['recent_messages'][0]['content'], '压力上限')
        self.assertEqual(second['rewritten_question'], '压力标准的压力上限')

    def test_ambiguity_or_invalid_history_rewrite_asks_clarification(self):
        llm = ScriptLLM({'needs_clarification': True, 'clarification': '请问是哪台设备？'})
        result = self.service(llm).query('它呢', self.ctx)
        self.assertEqual(result['answer'], '请问是哪台设备？')
        followup = self.service(ScriptLLM('bad JSON')).query('它的参数呢', self.ctx, session_id=result['session_id'])
        self.assertIn('补充完整', followup['answer'])
        self.assertEqual(followup['sources'], [])

    def test_summary_preserves_recent_messages_and_persists_on_restart(self):
        with self.state.conversation(self.ctx) as (sid, token, _):
            for number in range(6):
                self.state.save_turn(self.ctx, sid, token, '设备A问题' * 100, {'answer': '检查8MPa参数' * 100, 'sources': []})
        llm = ScriptLLM({'entities': ['设备A'], 'confirmed': ['用户询问8MPa参数'], 'open_questions': ['适用标准？']},
                       {'needs_clarification': True, 'clarification': '请补充设备工况'})
        service = self.service(llm)
        service.summary_threshold = 1000
        result = service.query('继续', self.ctx, session_id=sid)
        self.assertTrue(result['diagnostics']['summary_used'])
        saved = StateStore(self.engine, redis_url='').history(self.ctx, sid)
        self.assertIn('设备A', saved['summary']['entities'])
        self.assertEqual(len(saved['messages']), 10)
        with self.engine.connect() as conn:
            self.assertEqual(len(conn.execute(select(messages).where(messages.c.session_id == sid)).all()), 14)

    def test_outdated_linked_memory_is_not_injected(self):
        self.document('old', version='2020', start='2020-01-01')
        self.state.save_memory(self.ctx, 'reviewed_experience', '旧版经验', source_id='old')
        self.document('new', start=date.today().isoformat())
        llm = ScriptLLM({'needs_clarification': True, 'clarification': '请补充问题'})
        self.service(llm).query('继续', self.ctx)
        self.assertEqual(json.loads(llm.inputs[0][1][1])['memories'], [])

    def test_version_change_during_answer_is_rejected(self):
        self.document('new')
        state = self.state
        user = self.ctx
        class ChangingLLM(ScriptLLM):
            def invoke(self, payload):
                response = super().invoke(payload)
                if len(self.inputs) == 2:
                    state.change_status('new', user, 'withdrawn')
                return response
        result = self.service(ChangingLLM(plan(), '压力8MPa。[S1]', verified())).query(
            '压力上限', self.ctx, as_of=date(2026, 5, 1))
        self.assertFalse(result['citation_valid'])
        self.assertIn('版本发生变化', ' '.join(result['confidence']['reasons']))

    def test_current_equipment_snapshot_and_historical_refusal(self):
        steps = [{'question': '设备A参数', 'depends_on': [], 'tool': 'equipment', 'device_name': '设备A'}]
        with patch.object(self.state, 'equipment', return_value=[{'parameter': '压力', 'value': '8MPa'}]) as equipment:
            result = self.service(ScriptLLM(plan('设备A参数', steps), '设备A压力8MPa。[S1]', verified())).query('设备A参数', self.ctx)
            self.assertTrue(result['citation_valid'])
            self.assertEqual(equipment.call_args.args[1], '设备A')
            historical = self.service(ScriptLLM(plan('设备A参数', steps))).query('2025年设备A参数', self.ctx)
            self.assertEqual(historical['confidence']['level'], 'insufficient')
            self.assertIn('当前快照', historical['retrieval_steps'][0]['missing'])

    def test_relative_and_explicit_dates(self):
        today = date(2026, 10, 10)
        self.assertEqual(query_dates('去年和今年比较', today=today), [date(2025, 12, 31), today])
        self.assertEqual(query_dates('2025年标准', today=today), [date(2025, 12, 31)])
        self.assertEqual(query_dates('去年', as_of=date(2024, 1, 1), today=today), [date(2024, 1, 1)])
        self.assertEqual(query_dates('2025年1月1日标准', today=today), [date(2025, 1, 1)])
        self.assertEqual(query_dates('去年3月1日和2026-04-01', today=today), [date(2025, 3, 1), date(2026, 4, 1)])
        with self.assertRaises(ValueError):
            query_dates('2025年2月30日标准', today=today)
        with self.assertRaises(ValueError):
            query_dates('2023年2024年2025年', today=today)


if __name__ == '__main__':
    unittest.main()
