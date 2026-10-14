"""Retrieval calibration harness (Phase 3 workstream 3.3): dataset integrity and safety rails.

The harness itself is run manually with real local embeddings (python -m evals.rag_calibration). These tests never
compute real-embedding metrics: the stub embedder below only exercises code paths, and the harness must REFUSE the
project's fake provider so synthetic vectors can never be used to choose a production threshold."""

import hashlib
import math

import pytest

from app.core.config import settings
from app.services.ai.embeddings import FakeEmbeddingProvider
from app.services.ai.rag import retriever
from evals import rag_calibration as cal


@pytest.fixture(scope="module")
def dataset():
    return cal.load_dataset()


def test_dataset_is_versioned_and_marked_for_review(dataset):
    assert dataset["version"] == "1.0.0" and dataset["review_status"] == "PENDING_HR_REVIEW"
    assert "no personal data" in dataset["deidentification"].lower()


def test_dataset_has_both_splits_with_every_case_kind(dataset):
    for split in ("calibration", "holdout"):
        cases = [c for c in dataset["cases"] if c["split"] == split]
        assert sum(c["answerable"] for c in cases) >= 20
        assert sum(1 for c in cases if not c["answerable"] and not c.get("hard_negative")) >= 5
        assert sum(1 for c in cases if c.get("hard_negative")) >= 2


def test_every_corpus_policy_has_an_answerable_question(dataset):
    covered = {title for case in dataset["cases"] for title in case["expected_titles"]}
    assert covered == {document["title"] for document in dataset["corpus"]["documents"]}


def test_corpus_files_match_their_versioned_checksums(dataset):
    documents = cal.corpus_documents(dataset)
    assert len(documents) == 24
    assert all(text.startswith(("<!--", "# ")) for _, _, _, text in documents)


def test_changed_corpus_file_is_refused(dataset, tmp_path):
    for document in dataset["corpus"]["documents"]:
        if "file" in document:
            (tmp_path / document["file"]).write_text("# edited\n", encoding="utf-8")
    with pytest.raises(cal.CalibrationRefused, match="corpus file changed"):
        cal.corpus_documents(dataset, policy_dir=tmp_path)


def test_dataset_contains_no_seeded_personal_values(dataset):
    from tests.support.data import EMPLOYEE_SENSITIVE, USERS

    text = str(dataset).lower()
    for value in [*map(str, EMPLOYEE_SENSITIVE.values()), *(u.email for u in USERS.values())]:
        assert value.lower() not in text


def test_fake_embeddings_are_refused():
    with pytest.raises(cal.CalibrationRefused, match="NOT_RUN: real embeddings required"):
        cal.require_real_embedder(FakeEmbeddingProvider())


async def test_calibrate_refuses_under_the_test_configuration(tmp_path):
    assert settings.ai_embedding_provider == "fake"
    with pytest.raises(cal.CalibrationRefused, match="NOT_RUN"):
        await cal.calibrate(results_dir=tmp_path)
    assert not list(tmp_path.iterdir())  # no report written


def test_full_ranking_restores_the_relative_cutoff():
    original = retriever.RELATIVE_CUTOFF
    with cal.full_ranking():
        assert retriever.RELATIVE_CUTOFF == 0.0
    assert retriever.RELATIVE_CUTOFF == original


def test_proposed_threshold_breaks_ties_towards_abstaining():
    sweep = [{"threshold": 0.3, "balanced_score": 0.9}, {"threshold": 0.4, "balanced_score": 0.9},
             {"threshold": 0.5, "balanced_score": 0.8}]
    assert cal.propose_threshold(sweep) == 0.4


class StubEmbedder:
    """Deterministic bag-of-words vectors: exercises the harness code paths only (never used for thresholds)."""

    model_id = "stub-bow-for-tests"

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * 64
        for word in text.lower().split():
            vector[int(hashlib.sha256(word.strip(".,?!").encode()).hexdigest(), 16) % 64] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def embed(self, texts):
        return [self._vector(t) for t in texts]

    async def embed_query(self, text):
        return self._vector(text)


async def test_analysis_report_shape_and_split_discipline(dataset):
    outcomes = await cal.run_cases(dataset, StubEmbedder(), top_k=4, chunk_size=800, chunk_overlap=120)
    assert len(outcomes) == len(dataset["cases"])
    analysis = cal.analyse(outcomes, dataset, current_threshold=0.35, top_k=4)
    assert analysis["proposed_min_score"] == cal.propose_threshold(analysis["sweep"]["calibration"])
    assert set(analysis["ranking"]) == {"calibration", "holdout"}
    for row in analysis["sweep"]["holdout"]:
        assert row["answerable_cases"] + row["clear_negative_cases"] + row["hard_negative_cases"] == 40
        for key in ("answerable_hit_rate", "false_abstention_rate", "wrong_source_rate"):
            assert 0.0 <= row[key] <= 1.0
        assert math.isclose(row["answerable_hit_rate"] + row["false_abstention_rate"] + row["wrong_source_rate"], 1.0,
                            abs_tol=1e-3)
