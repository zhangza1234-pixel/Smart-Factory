import unittest
from types import SimpleNamespace
from rag import HybridRetriever, RAGService, RAGSettings, BM25Index, NO_EVIDENCE, authorized, tokenize


def context(role="工程师"):
    return SimpleNamespace(tenant_id="A", plant_id="P", line_id="L", role=role)


def record(uid, text, **metadata):
    return (uid, text, {"tenant_id": "A", "plant_id": "P", "line_id": "L",
                        "access_level": "操作员", "document_id": uid, **metadata})


class Store:
    def __init__(self, records, vectors=()):
        self.records, self.vectors = list(records), list(vectors)
        self.get_calls = 0

    def get(self, **kwargs):
        self.get_calls += 1
        batch = self.records[kwargs['offset']:kwargs['offset'] + kwargs['limit']]
        return dict(zip(("ids", "documents", "metadatas"), zip(*batch))) if batch else {"ids": []}

    def similarity_search_with_score(self, query, **kwargs):
        self.where = kwargs["filter"]
        return [(SimpleNamespace(page_content=text, metadata=meta), distance)
                for (_, text, meta), distance in self.vectors]


class LLM:
    def __init__(self, answer="建议检查保护气体纯度。[S1]"):
        self.calls, self.answer = 0, answer

    def invoke(self, messages):
        self.calls += 1
        return SimpleNamespace(content=self.answer)


class RetrievalTests(unittest.TestCase):
    def test_chinese_and_fault_tokens(self):
        self.assertIn("f0012", tokenize("F0012故障"))
        self.assertIn("焊接", tokenize("激光焊接机"))

    def test_bm25_ranks_exact_code(self):
        index = BM25Index([record("a", "F0012故障检查气体纯度"), record("b", "F0099故障检查冷却水")])
        self.assertEqual(index.search("F0012故障", 2)[0][0][0], "a")

    def test_filters_enforced_even_if_store_returns_foreign_records(self):
        good = record("ok", "激光焊接故障检查保护气体")
        bad = [record("tenant", good[1], tenant_id="B"), record("plant", good[1], plant_id="other"),
               record("line", good[1], line_id="other"), record("acl", good[1], access_level="专家"),
               record("legacy", good[1], line_id=None), record("unknown", good[1], access_level="admin")]
        store = Store([good] + bad, [(item, 0.1) for item in [good] + bad])
        sources, _ = HybridRetriever(store).retrieve("焊接故障", context())
        self.assertEqual([item['id'] for item in sources], ["ok"])
        self.assertEqual(store.where['$and'][0], {"tenant_id": {"$eq": "A"}})

    def test_plant_shared_scope(self):
        self.assertTrue(authorized(record("a", "text", line_id="")[2], context()))

    def test_code_match_required_even_for_near_vector(self):
        wrong = record("wrong", "F0013故障检查冷却水")
        right = record("right", "F0012故障检查气体纯度")
        sources, _ = HybridRetriever(Store([wrong, right], [(wrong, 0.01)])).retrieve("F0012故障", context())
        self.assertEqual([item['id'] for item in sources], ["right"])

    def test_irrelevant_query_does_not_call_llm(self):
        item = record("a", "激光焊接机保养")
        llm = LLM()
        result = RAGService(HybridRetriever(Store([item], [(item, 5.0)])), llm).query("天气", context())
        self.assertEqual(result['answer'], NO_EVIDENCE)
        self.assertEqual(llm.calls, 0)

    def test_empty_store(self):
        self.assertEqual(HybridRetriever(Store([])).retrieve("问题", context())[0], [])

    def test_deduplicate_and_bound_context(self):
        first = record("a", "激光焊接维护清洁镜头" * 40)
        second = record("b", first[1])
        sources, diagnostic = HybridRetriever(Store([first, second]), RAGSettings(context_chars=200)).retrieve("焊接", context())
        self.assertEqual(len(sources), 1)
        self.assertEqual(diagnostic['context_chars'], 200)

    def test_cache_invalidation(self):
        store = Store([record("a", "焊接操作")])
        retriever = HybridRetriever(store)
        retriever.retrieve("焊接", context())
        retriever.retrieve("焊接", context())
        self.assertEqual(store.get_calls, 1)
        retriever.invalidate()
        retriever.retrieve("焊接", context())
        self.assertEqual(store.get_calls, 2)

    def test_invalid_citation_rejected(self):
        service = RAGService(HybridRetriever(Store([record("a", "焊接维护")])), LLM("答案 [S99]"))
        result = service.query("焊接", context())
        self.assertFalse(result['citation_valid'])
        self.assertNotIn("[S99]", result['answer'])

    def test_valid_citation_and_compatibility(self):
        result = RAGService(HybridRetriever(Store([record("a", "保护气体纯度")])), LLM()).query("气体", context())
        self.assertTrue(result['citation_valid'])
        self.assertEqual(result['source_data'][0]['tool'], 'chroma_search')

    def test_settings_validation(self):
        with self.assertRaises(ValueError):
            RAGSettings(candidate_k=2, top_k=5)


if __name__ == "__main__":
    unittest.main()
