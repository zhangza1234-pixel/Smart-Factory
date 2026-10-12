"""Offline API and ingestion regressions: no MySQL or paid model calls."""
from io import BytesIO
import json
import os
import unittest
import tempfile
from unittest.mock import patch
from fastapi import UploadFile, HTTPException
from fastapi.testclient import TestClient
from langchain_core.documents import Document
import mains
from ingestion import load_chunks, prepare_chunks
from rag import HybridRetriever
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from state_store import StateStore


CTX = {"tenant_id": "A", "plant_id": "P", "line_id": "L", "role": "专家"}


class FakeStore:
    def __init__(self):
        self.records = {}

    def get(self, ids, **kwargs):
        return {"ids": [uid for uid in ids if uid in self.records]}

    def add_texts(self, texts, metadatas, ids):
        self.records.update({uid: (text, meta) for uid, text, meta in zip(ids, texts, metadatas)})


class APITests(unittest.TestCase):
    def setUp(self):
        # These tests exercise endpoint logic; test_auth covers the real identity boundary.
        mains.app.dependency_overrides[mains.authorize] = lambda: None
        self.addCleanup(mains.app.dependency_overrides.pop, mains.authorize)
        self.client = TestClient(mains.app)
        engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        self.state = StateStore(engine, redis_url='')
        self.state_patch = patch.object(mains, 'init_state', return_value=self.state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(engine.dispose)

    def test_health_without_mysql_or_key(self):
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_invalid_question_and_role(self):
        self.assertEqual(self.client.post('/query', json={"question": "  ", "user_ctx": CTX}).status_code, 422)
        self.assertEqual(self.client.post('/query', json={"question": "a", "user_ctx": {**CTX, 'role': 'admin'}}).status_code, 422)

    def test_missing_config_returns_503(self):
        with patch.dict(os.environ, {"DASHSCOPE_API_KEY": ""}), patch.object(mains, 'rag_service', None):
            self.assertEqual(self.client.post('/query', json={"question": "焊接", "user_ctx": CTX}).status_code, 503)

    def test_greeting_and_history_work_without_model_key_or_vector_store(self):
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY': ''}), patch.object(mains, 'init_rag') as rag:
            first = self.client.post('/query', json={'question': '你好', 'user_ctx': CTX})
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json()['intent'], 'chat')
            recall = self.client.post('/query', json={'question': '我刚刚问的什么', 'user_ctx': CTX,
                'session_id': first.json()['session_id']})
            self.assertEqual(recall.status_code, 200, recall.text)
            self.assertIn('你好', recall.json()['answer'])
            self.assertEqual(recall.json()['intent'], 'history')
            self.assertEqual(recall.json()['confidence'], {})
            rag.assert_not_called()

    def upload(self, ctx=CTX, filename="manual.txt", content=None):
        return self.client.post('/upload-document', data={"user_ctx": json.dumps(ctx), "title": "焊接手册",
            "category": "故障手册", "access_level": "操作员"},
            files={"file": (filename, content if content is not None else "F0012故障检查保护气体纯度".encode())})

    def test_upload_retry_deduplicates_and_preserves_metadata(self):
        store = FakeStore()
        with patch.object(mains, 'init_chroma', return_value=store):
            first, second = self.upload(), self.upload()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()['duplicate'])
        self.assertTrue(second.json()['duplicate'])
        self.assertEqual(len(store.records), first.json()['chunks'])
        metadata = next(iter(store.records.values()))[1]
        self.assertEqual(metadata['filename'], 'manual.txt')
        self.assertEqual(metadata['tenant_id'], 'A')

    def test_upload_access_and_format_validation(self):
        self.assertEqual(self.upload({**CTX, 'role': '操作员'}).status_code, 403)
        self.assertEqual(self.upload(filename='bad.exe').status_code, 415)
        self.assertEqual(self.upload(content=b'').status_code, 422)
        self.assertEqual(self.client.post('/upload-document', data={"user_ctx": "{}", "title": "a",
            "category": "a", "access_level": "操作员"}, files={"file": ('a.txt', b'a')}).status_code, 422)

    def test_upload_empty_title_uses_filename(self):
        for title in (None, '', '   ', ' 自定义标题 '):
            with self.subTest(title=title):
                store = FakeStore()
                data = {"user_ctx": json.dumps(CTX), "category": "标准", "access_level": "操作员"}
                if title is not None:
                    data['title'] = title
                with patch.object(mains, 'init_chroma', return_value=store):
                    response = self.client.post('/upload-document', data=data,
                        files={'file': ('设备手册.md', '# 手册\n检查气体'.encode())})
                self.assertEqual(response.status_code, 200, response.text)
                expected = '自定义标题' if title and title.strip() else '设备手册'
                self.assertEqual(response.json()['title'], expected)
                self.assertTrue(store.records)
                for _, metadata in store.records.values():
                    self.assertEqual(metadata['title'], expected)

    def test_gbk_decoding_and_size_limit(self):
        chunks, _ = load_chunks(UploadFile(filename='a.txt', file=BytesIO('焊接故障'.encode('gbk'))))
        self.assertEqual(chunks[0].page_content, '焊接故障')
        with self.assertRaises(HTTPException) as error:
            load_chunks(UploadFile(filename='a.txt', file=BytesIO(b'1234')), max_bytes=3)
        self.assertEqual(error.exception.status_code, 413)

    def test_document_identity_is_scoped(self):
        chunks = [Document(page_content='text', metadata={'page': 1})]
        first = prepare_chunks(chunks, 'hash', CTX)[0]
        other = prepare_chunks(chunks, 'hash', {**CTX, 'tenant_id': 'B'})[0]
        self.assertNotEqual(first, other)

    def test_split_preview_and_strategy_validation(self):
        response = self.client.post('/test_file-upload',
            data={'split_strategy': 'structured'},
            files={'file': ('a.md', '# 手册\n## 焊接\n检查气体\n## 冷却\n检查冷却水'.encode())})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['chunk_count'], 2)
        self.assertEqual(response.json()['preview'][0]['metadata']['section_path'], '手册 > 焊接')
        invalid = self.client.post('/test_file-upload', data={'split_strategy': 'unknown'}, files={'file': ('a.txt', b'text')})
        self.assertEqual(invalid.status_code, 422)

    def test_partial_vector_write_remains_draft_and_cannot_publish(self):
        store = FakeStore()
        with patch.object(mains, 'init_chroma', return_value=store), patch.object(store, 'add_texts', side_effect=RuntimeError('offline')):
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        row = self.state.catalog(mains.UserContext(**CTX))[0]
        self.assertEqual(row['status'], 'draft')
        with patch.object(mains, 'init_chroma', return_value=store):
            response = self.client.post(f"/documents/{row['id']}/status", json={'user_ctx': CTX, 'status': 'published'})
        self.assertEqual(response.status_code, 409)

    def test_api_version_conflict_and_historical_selection(self):
        store = FakeStore()
        def upload(version, start, text):
            return self.client.post('/upload-document', data={'user_ctx': json.dumps(CTX), 'title': '压力标准',
                'category': '标准', 'access_level': '操作员', 'family_name': '压力标准', 'version': version,
                'effective_from': start}, files={'file': ('standard.md', text.encode())})
        with patch.object(mains, 'init_chroma', return_value=store):
            old = upload('2025', '2025-01-01', '压力上限10MPa')
            new = upload('2026', '2026-01-01', '压力上限8MPa')
            conflict = upload('2026', '2026-01-01', '另一份不同内容')
        self.assertEqual(old.status_code, 200, old.text)
        self.assertEqual(new.status_code, 200, new.text)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(set(self.state.selected_versions(mains.UserContext(**CTX), mains.date(2025, 12, 31))), {old.json()['document_id']})
        self.assertEqual(set(self.state.selected_versions(mains.UserContext(**CTX), mains.date(2026, 6, 1))), {new.json()['document_id']})

    def test_memory_api_is_user_scoped_and_explicit(self):
        first = self.client.post('/memories', json={'user_ctx': {**CTX, 'user_id': 'alice'}, 'content': '简短回答'})
        self.assertEqual(first.status_code, 200)
        other = self.client.post('/memories/list', json={'user_ctx': {**CTX, 'user_id': 'bob'}})
        self.assertEqual(other.json()['memories'], [])
        delete = self.client.post(f"/memories/{first.json()['id']}/delete", json={'user_ctx': {**CTX, 'user_id': 'bob'}})
        self.assertEqual(delete.status_code, 404)
        reviewed = self.client.post('/memories', json={'user_ctx': {**CTX, 'role': '操作员'}, 'kind': 'reviewed_experience', 'content': '经验'})
        self.assertEqual(reviewed.status_code, 403)

    def test_query_date_and_session_validation(self):
        for fields in ({'as_of': '2026-01-01', 'comparison_dates': ['2025-01-01', '2026-01-01']},
                       {'comparison_dates': ['2026-01-01', '2026-01-01']}, {'session_id': 'bad-id'}):
            response = self.client.post('/query', json={'question': '压力', 'user_ctx': CTX, **fields})
            self.assertEqual(response.status_code, 422)

    def test_free_sql_disabled_and_fault_values_bound(self):
        self.assertEqual(self.client.post('/execute-sql', json={'sql': 'SELECT * FROM rag_messages', 'user_ctx': CTX}).status_code, 410)
        injection = "' OR 1=1 --"
        with patch.object(mains, 'execute_sql', return_value=[]) as execute:
            response = self.client.post('/query-faults', json={'user_ctx': CTX, 'fault_code': injection})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(injection, execute.call_args.args[0])
        self.assertEqual(execute.call_args.args[2], ['A', '%' + injection + '%'])

    def test_unmanaged_vector_records_are_excluded(self):
        from tests.test_rag import Store, record
        store = Store([record('legacy', '旧版压力上限10MPa')])
        rag = type('RAG', (), {'retriever': HybridRetriever(store)})()
        with patch.object(mains, 'init_rag', return_value=rag):
            response = self.client.post('/retrieve', json={'question': '压力上限', 'user_ctx': CTX})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['sources'], [])


class ChromaIntegrationTests(unittest.TestCase):
    def test_real_chroma_filter_upsert_and_hybrid_retrieval(self):
        from langchain_chroma import Chroma
        from langchain_core.embeddings import Embeddings
        from chromadb.config import Settings

        class LocalEmbeddings(Embeddings):
            def embed_documents(self, texts):
                return [[float(text.count(char)) for char in '焊接气体故障水冷'] for text in texts]

            def embed_query(self, text):
                return self.embed_documents([text])[0]

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            store = Chroma(collection_name='test_factory', persist_directory=directory,
                           embedding_function=LocalEmbeddings(),
                           client_settings=Settings(anonymized_telemetry=False))
            metadata = {**CTX, 'access_level': '操作员', 'filename': 'manual.txt', 'title': '故障手册'}
            chunks = [Document(page_content='F0012故障检查气体纯度', metadata={'page': 1})]
            _, ids, texts, metadatas = prepare_chunks(chunks, 'hash', metadata)
            store.add_texts(texts=texts, metadatas=metadatas, ids=ids)
            store.add_texts(texts=texts, metadatas=metadatas, ids=ids)
            store.add_texts(texts=texts, metadatas=[{**metadatas[0], 'tenant_id': 'B'}], ids=['foreign'])
            self.assertEqual(len(store.get(include=[])['ids']), 2)
            sources, _ = HybridRetriever(store).retrieve('F0012故障', mains.UserContext(**CTX))
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]['metadata']['tenant_id'], 'A')
            self.assertEqual(sources[0]['metadata']['page'], 1)
            store.delete_collection()


if __name__ == '__main__':
    unittest.main()
