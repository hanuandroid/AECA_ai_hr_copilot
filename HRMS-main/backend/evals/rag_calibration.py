"""Policy RAG retrieval calibration with REAL local embeddings (production hardening Phase 3, workstream 3.3).

    cd HRMS-main/backend
    AI_EMBEDDING_PROVIDER=fastembed AI_EMBEDDING_CACHE_DIR=<model cache> python -m evals.rag_calibration

What it does:
- builds the versioned corpus from evals/datasets/policy_retrieval_v1.json in memory (checksums verified against
  data/policies), chunks it with the production chunker and embeds it with the configured local embedding model;
- runs the production PolicyRetriever (hybrid score, top_k, relative cutoff) for every question at a sweep of
  min_score thresholds, plus threshold-free ranking metrics;
- picks a PROPOSED threshold on the calibration split only and reports it on the holdout split.

What it never does: touch any database, call an LLM or any API that needs a key, download a model (offline by
default; --allow-download is an explicit opt-in), change AI_RAG_MIN_SCORE, or run on fake/hash embeddings (it
refuses: synthetic vectors must not be used to tune production retrieval). Acceptance targets stay PENDING.
"""

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DATASET = BACKEND / "evals" / "datasets" / "policy_retrieval_v1.json"
POLICY_DIR = BACKEND / "data" / "policies"
DEFAULT_RESULTS = BACKEND / "evals" / "results"
THRESHOLDS = [round(0.20 + 0.025 * step, 3) for step in range(21)]  # 0.200 .. 0.700
# Offline retrieval-quality tooling over the whole demo corpus (every policy is ALL), not a user path. RBAC step 7
# requires audiences on every retrieval, so the full set is passed explicitly here.
CALIBRATION_AUDIENCES = frozenset({"ALL", "MANAGERS", "ADMINS"})


class CalibrationRefused(RuntimeError):
    """The run would not produce real-embedding evidence (fake provider, model unavailable offline, bad corpus)."""


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    split: str
    answerable: bool
    hard_negative: bool
    top_score: float
    expected_rank: int | None  # 1-based rank of the first expected title at min_score=0, None if absent
    retrieved_by_threshold: dict[float, list[str]]  # titles returned at each threshold


def load_dataset(path: Path = DATASET) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = [case["id"] for case in data["cases"]]
    if len(ids) != len(set(ids)):
        raise CalibrationRefused("duplicate case ids")
    titles = {document["title"] for document in data["corpus"]["documents"]}
    for case in data["cases"]:
        if case["split"] not in ("calibration", "holdout"):
            raise CalibrationRefused(f"{case['id']}: unknown split")
        if case["answerable"] != bool(case["expected_titles"]) or not set(case["expected_titles"]) <= titles:
            raise CalibrationRefused(f"{case['id']}: expected titles inconsistent with the corpus")
    return data


def corpus_documents(data: dict, policy_dir: Path = POLICY_DIR) -> list[tuple[str, str, str, str]]:
    """(title, category, source_filename, markdown) for every corpus document; file checksums must match."""
    documents = []
    for index, document in enumerate(data["corpus"]["documents"], start=1):
        if "file" in document:
            text = (policy_dir / document["file"]).read_text(encoding="utf-8").replace("\r\n", "\n")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != document["sha256_lf"]:
                raise CalibrationRefused(f"corpus file changed: {document['file']} (bump the dataset version)")
            filename = document["file"]
        else:
            text = f"# {document['title']}\n\n{document['text']}\n"
            filename = f"seed_policy_{index:02d}.md"
        documents.append((document["title"], document["category"], filename, text))
    return documents


def build_chunks(documents, chunk_size: int, chunk_overlap: int):
    from app.services.ai.rag.chunker import chunk_document
    from app.services.ai.rag.loader import normalise_text
    from app.services.ai.rag.models import PolicyDocument

    chunks = []
    for policy_id, (title, category, filename, raw) in enumerate(documents, start=1):
        text = normalise_text(raw)
        document = PolicyDocument(policy_id=policy_id, title=title, category=category, source_filename=filename,
                                  text=text, checksum=hashlib.sha256(text.encode("utf-8")).hexdigest())
        chunks.extend(chunk_document(document, chunk_size, chunk_overlap))
    return chunks


class InMemoryStore:
    """Read-only PolicyVectorStore over pre-embedded chunks (no database)."""

    def __init__(self, embedding_model: str, stored):
        self.embedding_model = embedding_model
        self._stored = stored

    async def all_chunks(self, *, audiences: frozenset[str]):
        # The calibration corpus is the demo policy set, which is entirely audience ALL (RBAC step 7).
        return list(self._stored) if "ALL" in audiences else []


class CachingEmbedder:
    def __init__(self, inner):
        self._inner = inner
        self.model_id = inner.model_id
        self._queries: dict[str, list[float]] = {}

    async def embed(self, texts):
        return await self._inner.embed(texts)

    async def embed_query(self, text):
        if text not in self._queries:
            self._queries[text] = await self._inner.embed_query(text)
        return self._queries[text]


@contextmanager
def full_ranking():
    """Disable the retriever's relative cutoff for the threshold-free ranking pass only (restored afterwards); the
    threshold sweep always runs the production retriever unchanged."""
    from app.services.ai.rag import retriever

    original = retriever.RELATIVE_CUTOFF
    retriever.RELATIVE_CUTOFF = 0.0
    try:
        yield
    finally:
        retriever.RELATIVE_CUTOFF = original


def require_real_embedder(embedder) -> None:
    from app.services.ai.embeddings import FakeEmbeddingProvider

    if isinstance(embedder, FakeEmbeddingProvider) or "fake" in str(embedder.model_id).lower():
        raise CalibrationRefused("NOT_RUN: real embeddings required (AI_EMBEDDING_PROVIDER=fastembed); "
                                 "synthetic vectors must not be used to tune retrieval thresholds")


async def run_cases(data: dict, embedder, *, top_k: int, chunk_size: int, chunk_overlap: int) -> list[CaseOutcome]:
    from app.services.ai.rag.retriever import PolicyRetriever
    from app.services.ai.rag.vector_store import StoredChunk

    chunks = build_chunks(corpus_documents(data), chunk_size, chunk_overlap)
    vectors = await embedder.embed([chunk.embedding_text() for chunk in chunks])
    store = InMemoryStore(embedder.model_id, [StoredChunk(chunk=c, embedding=v) for c, v in zip(chunks, vectors)])
    cached = CachingEmbedder(embedder)

    outcomes = []
    for case in data["cases"]:
        with full_ranking():  # threshold-free pass: every chunk, no relative cutoff
            ranked = await PolicyRetriever(store, cached, top_k=len(chunks), min_score=0.0).retrieve(case["question"], audiences=CALIBRATION_AUDIENCES)
        ranked_titles = list(dict.fromkeys(item.chunk.policy_title for item in ranked))
        expected_rank = next((i for i, title in enumerate(ranked_titles, start=1) if title in case["expected_titles"]), None)
        by_threshold = {}
        for threshold in THRESHOLDS:
            results = await PolicyRetriever(store, cached, top_k=top_k, min_score=threshold).retrieve(case["question"], audiences=CALIBRATION_AUDIENCES)
            by_threshold[threshold] = [item.chunk.policy_title for item in results]
        outcomes.append(CaseOutcome(case["id"], case["split"], case["answerable"], bool(case.get("hard_negative")),
                                    ranked[0].score if ranked else 0.0, expected_rank, by_threshold))
    return outcomes


def metrics_at(outcomes: list[CaseOutcome], threshold: float, expected: dict[str, list[str]]) -> dict:
    answerable = [o for o in outcomes if o.answerable]
    negatives = [o for o in outcomes if not o.answerable]
    clear_negatives = [o for o in negatives if not o.hard_negative]
    hits = sum(1 for o in answerable if set(o.retrieved_by_threshold[threshold]) & set(expected[o.case_id]))
    abstained = sum(1 for o in answerable if not o.retrieved_by_threshold[threshold])
    correct_abstain = sum(1 for o in clear_negatives if not o.retrieved_by_threshold[threshold])
    hard_abstain = sum(1 for o in negatives if o.hard_negative and not o.retrieved_by_threshold[threshold])

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    hit_rate, abstain_rate = rate(hits, len(answerable)), rate(correct_abstain, len(clear_negatives))
    return {
        "threshold": threshold,
        "answerable_cases": len(answerable),
        "answerable_hit_rate": hit_rate,  # expected policy retrieved, not abstained
        "false_abstention_rate": rate(abstained, len(answerable)),
        "wrong_source_rate": rate(len(answerable) - hits - abstained, len(answerable)),
        "clear_negative_cases": len(clear_negatives),
        "correct_abstention_rate": abstain_rate,
        "false_acceptance_rate": rate(len(clear_negatives) - correct_abstain, len(clear_negatives)),
        "hard_negative_cases": len(negatives) - len(clear_negatives),
        "hard_negative_abstention_rate": rate(hard_abstain, len(negatives) - len(clear_negatives)),
        "balanced_score": round((hit_rate + abstain_rate) / 2, 4) if hit_rate is not None and abstain_rate is not None else None,
    }


def ranking_metrics(outcomes: list[CaseOutcome], top_k: int) -> dict:
    answerable = [o for o in outcomes if o.answerable]
    negatives = [o for o in outcomes if not o.answerable and not o.hard_negative]
    pairs = [(a.top_score, n.top_score) for a in answerable for n in negatives]
    auroc = (sum(1.0 if a > n else 0.5 if a == n else 0.0 for a, n in pairs) / len(pairs)) if pairs else None

    def summary(values: list[float]) -> dict | None:
        return {"min": round(min(values), 4), "median": round(statistics.median(values), 4), "max": round(max(values), 4)} if values else None

    return {
        f"recall_at_{top_k}": round(sum(1 for o in answerable if o.expected_rank and o.expected_rank <= top_k) / len(answerable), 4) if answerable else None,
        "recall_at_1": round(sum(1 for o in answerable if o.expected_rank == 1) / len(answerable), 4) if answerable else None,
        "mrr": round(sum(1 / o.expected_rank for o in answerable if o.expected_rank) / len(answerable), 4) if answerable else None,
        "top_score_answerable": summary([o.top_score for o in answerable]),
        "top_score_clear_negative": summary([o.top_score for o in negatives]),
        "top_score_hard_negative": summary([o.top_score for o in outcomes if o.hard_negative]),
        "auroc_answerable_vs_clear_negative": round(auroc, 4) if auroc is not None else None,
    }


def propose_threshold(calibration_sweep: list[dict]) -> float:
    """Highest balanced score on the calibration split; ties go to the HIGHER threshold (abstain more)."""
    return max(calibration_sweep, key=lambda row: (row["balanced_score"] or 0.0, row["threshold"]))["threshold"]


def analyse(outcomes: list[CaseOutcome], data: dict, *, current_threshold: float, top_k: int) -> dict:
    expected = {case["id"]: case["expected_titles"] for case in data["cases"]}
    splits = {name: [o for o in outcomes if o.split == name] for name in ("calibration", "holdout")}
    sweep = {name: [metrics_at(cases, t, expected) for t in THRESHOLDS] for name, cases in splits.items()}
    proposed = propose_threshold(sweep["calibration"])
    current = min(THRESHOLDS, key=lambda t: abs(t - current_threshold))
    by_threshold = {name: {row["threshold"]: row for row in rows} for name, rows in sweep.items()}
    return {
        "proposed_min_score": proposed,
        "current_min_score": current_threshold,
        "holdout_at_proposed": by_threshold["holdout"][proposed],
        "holdout_at_current": by_threshold["holdout"][current],
        "ranking": {name: ranking_metrics(cases, top_k) for name, cases in splits.items()},
        "sweep": sweep,
        "misses_at_proposed": [
            {"id": o.case_id, "split": o.split, "answerable": o.answerable, "hard_negative": o.hard_negative,
             "top_score": round(o.top_score, 4), "retrieved": o.retrieved_by_threshold[proposed][:3]}
            for o in outcomes
            if (o.answerable and not set(o.retrieved_by_threshold[proposed]) & set(expected[o.case_id]))
            or (not o.answerable and o.retrieved_by_threshold[proposed])
        ],
    }


def render_markdown(report: dict) -> str:
    holdout_p, holdout_c = report["analysis"]["holdout_at_proposed"], report["analysis"]["holdout_at_current"]
    lines = [
        f"# Policy RAG retrieval calibration ({report['dataset']} v{report['dataset_version']}, {report['embedding_model']})",
        "",
        f"Status: **PROPOSAL ONLY**. `AI_RAG_MIN_SCORE` is unchanged ({report['analysis']['current_min_score']}). "
        "Acceptance targets: **PENDING** dataset review and approval.",
        "",
        f"Proposed min_score (chosen on calibration split): **{report['analysis']['proposed_min_score']}**",
        "",
        "| Holdout metric | at current | at proposed |",
        "|---|---|---|",
    ]
    for key in ("answerable_hit_rate", "false_abstention_rate", "wrong_source_rate", "correct_abstention_rate",
                "false_acceptance_rate", "hard_negative_abstention_rate"):
        lines.append(f"| {key} | {holdout_c[key]} | {holdout_p[key]} |")
    lines += ["", "Ranking (threshold-free):", "", "```", json.dumps(report["analysis"]["ranking"], indent=2), "```"]
    return "\n".join(lines) + "\n"


async def calibrate(*, allow_download: bool = False, results_dir: Path = DEFAULT_RESULTS) -> dict:
    if not allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # never fetch a model implicitly
    from app.core.config import settings
    from app.services.ai.providers import build_embedding_provider

    data = load_dataset()
    embedder = build_embedding_provider(settings)
    require_real_embedder(embedder)
    try:
        outcomes = await run_cases(data, embedder, top_k=settings.ai_rag_top_k, chunk_size=settings.ai_policy_chunk_size,
                                   chunk_overlap=settings.ai_policy_chunk_overlap)
    except CalibrationRefused:
        raise
    except Exception as exc:  # model missing offline, ONNX runtime error, ...
        raise CalibrationRefused(f"NOT_RUN: embedding model unavailable ({type(exc).__name__})") from exc

    report = {
        "dataset": data["dataset"], "dataset_version": data["version"], "review_status": data["review_status"],
        "corpus_version": data["corpus"]["version"], "embedding_model": embedder.model_id,
        "top_k": settings.ai_rag_top_k, "chunk_size": settings.ai_policy_chunk_size,
        "chunk_overlap": settings.ai_policy_chunk_overlap, "status": "PROPOSAL_ONLY_TARGETS_PENDING",
        "cases": {split: sum(1 for c in data["cases"] if c["split"] == split) for split in ("calibration", "holdout")},
        "analysis": analyse(outcomes, data, current_threshold=settings.ai_rag_min_score, top_k=settings.ai_rag_top_k),
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "rag_calibration.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    (results_dir / "rag_calibration.md").write_text(render_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--allow-download", action="store_true", help="permit fetching the embedding model (no HR data is sent)")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(calibrate(allow_download=args.allow_download, results_dir=args.results_dir))
    except CalibrationRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
