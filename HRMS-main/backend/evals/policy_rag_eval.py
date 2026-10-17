"""Lightweight deterministic Policy RAG evaluation (foundation for the Phase 10 eval framework).

Metrics:
- retrieval_hit: an expected source (title[/section]) is among the retrieved chunks
- citation_validity: every returned source corresponds to a retrieved chunk
- groundedness: answerable cases return grounded=true with sources and an expected fact
- refusal: unanswerable cases return grounded=false without sources

This is NOT a full LLM evaluation: offline it uses ExtractiveFakeLLM, which answers only by quoting
sentences from the retrieved context, so it measures the pipeline (retrieval, prompting contract,
guard, citations) rather than model quality. Real-model runs belong to Phase 10.
"""

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.services.ai.authorization import AIUserContext, policy_audiences
from app.services.ai.errors import AIInvalidOutputError
from app.services.ai.llm.base import LLMMessage, LLMResponse, StructuredT
from app.services.ai.rag.retriever import PolicyRetriever, content_terms
from app.services.ai.rag.service import PolicyRagService

DATASET = Path(__file__).with_name("policy_eval.json")
_DOC_RE = re.compile(r'<policy_document id="(S\d+)"[^>]*>\n(.*?)\n</policy_document>', re.DOTALL)
_QUESTION_RE = re.compile(r"<user_question>\n(.*?)\n</user_question>", re.DOTALL)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")


class ExtractiveFakeLLM:
    """Deterministic stand-in for the LLM that can only quote the supplied context."""

    name = "extractive-fake"
    model = "extractive-fake"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages: list[LLMMessage], **_: object) -> LLMResponse:
        raise NotImplementedError

    async def generate_structured(self, messages: list[LLMMessage], schema: type[StructuredT], **_: object) -> StructuredT:
        self.calls += 1
        prompt = messages[-1].content
        question_match = _QUESTION_RE.search(prompt)
        if question_match is None:
            raise AIInvalidOutputError()
        question_terms = content_terms(html.unescape(question_match.group(1)))
        best: tuple[int, str, list[str]] | None = None
        for label, body in _DOC_RE.findall(prompt):
            sentences = [s.strip() for s in _SENTENCE_RE.split(html.unescape(body)) if s.strip()]
            ranked = sorted(sentences, key=lambda s: -len(question_terms & content_terms(s)))
            overlap = len(question_terms & content_terms(ranked[0])) if ranked else 0
            if overlap and (best is None or overlap > best[0]):
                best = (overlap, label, ranked[:2])
        if best is None:
            return schema.model_validate({"answer": "The policies do not cover this.", "grounded": False, "citations": []})
        _, label, sentences = best
        return schema.model_validate({"answer": " ".join(sentences), "grounded": True, "citations": [label]})


@dataclass
class CaseResult:
    id: str
    behavior: str
    retrieval_hit: bool | None
    citation_valid: bool
    grounded_ok: bool | None
    refusal_ok: bool | None
    answer: str


@dataclass
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)

    def _rate(self, values: list[bool]) -> float:
        return round(sum(values) / len(values), 3) if values else 1.0

    @property
    def metrics(self) -> dict[str, float]:
        return {
            "retrieval_hit": self._rate([r.retrieval_hit for r in self.results if r.retrieval_hit is not None]),
            "citation_validity": self._rate([r.citation_valid for r in self.results]),
            "groundedness": self._rate([r.grounded_ok for r in self.results if r.grounded_ok is not None]),
            "refusal": self._rate([r.refusal_ok for r in self.results if r.refusal_ok is not None]),
        }


def load_cases(path: Path = DATASET) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _matches(expected: dict, title: str, section: str) -> bool:
    return expected["title"] == title and expected.get("section", section) == section


async def evaluate(
    cases: list[dict],
    service: PolicyRagService,
    retriever: PolicyRetriever,
    contexts: dict[str, AIUserContext],
) -> EvalReport:
    report = EvalReport()
    for case in cases:
        retrieved = await retriever.retrieve(case["question"], audiences=policy_audiences(contexts[case["role"]]))
        retrieved_ids = {item.chunk.chunk_id for item in retrieved}
        answer = await service.answer(contexts[case["role"]], case["question"])
        citation_valid = all(source.chunk_id in retrieved_ids for source in answer.sources)

        if case["expected_behavior"] == "answer_with_source":
            hit = any(_matches(e, i.chunk.policy_title, i.chunk.section) for e in case["expected_sources"] for i in retrieved)
            fact = any(f.lower() in answer.answer.lower() for f in case["answer_contains_any"]) if case["answer_contains_any"] else True
            source_ok = any(_matches(e, s.title, s.section) for e in case["expected_sources"] for s in answer.sources)
            report.results.append(
                CaseResult(case["id"], case["expected_behavior"], hit, citation_valid,
                           answer.grounded and bool(answer.sources) and fact and source_ok, None, answer.answer)
            )
        else:
            refused = not answer.grounded and not answer.sources
            report.results.append(CaseResult(case["id"], case["expected_behavior"], None, citation_valid, None, refused, answer.answer))
    return report
