"""Evaluate retrieval against a local JSONL golden set; no generation calls."""
import argparse
import json
from pathlib import Path
from statistics import mean
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def evaluate(retriever, cases, context_type, state=None):
    rows = []
    for case in cases:
        from datetime import date
        from workflow import query_dates
        ctx = context_type(**case['user_ctx'])
        sources, elapsed = [], 0
        for day in query_dates(case['question'], date.fromisoformat(case['as_of']) if case.get('as_of') else None,
                               [date.fromisoformat(item) for item in case.get('comparison_dates', [])]):
            batch, diagnostics = retriever.retrieve(case['question'], ctx,
                eligible=state.selected_versions(ctx, day) if state else None)
            sources.extend(batch)
            elapsed += diagnostics['retrieval_ms']
        expected = set(case["expected_filenames"])
        found = [source["metadata"].get("filename", "") for source in sources]
        relevant = expected.intersection(found)
        rank = next((index for index, filename in enumerate(found, 1) if filename in expected), 0)
        rows.append({"question": case["question"], "expected": sorted(expected), "retrieved": found,
                     "recall": len(relevant) / len(expected) if expected else None,
                     "reciprocal_rank": 1 / rank if rank else 0,
                     "empty_correct": not found if not expected else None,
                     "retrieval_ms": elapsed})
    answerable = [row for row in rows if row["recall"] is not None]
    unanswerable = [row for row in rows if row["empty_correct"] is not None]
    return {"cases": len(rows),
            "recall_at_k": mean(row["recall"] for row in answerable) if answerable else None,
            "mrr": mean(row["reciprocal_rank"] for row in answerable) if answerable else None,
            "empty_accuracy": mean(row["empty_correct"] for row in unanswerable) if unanswerable else None,
            "details": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evaluation-report.json"))
    args = parser.parse_args()
    from mains import init_rag, init_state, UserContext
    cases = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not cases:
        parser.error("评测集不能为空")
    result = evaluate(init_rag().retriever, cases, UserContext, init_state())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "details"}, ensure_ascii=False))
