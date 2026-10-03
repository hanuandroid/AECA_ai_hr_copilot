"""Hybrid semantic + lexical retrieval over policy chunks.

Why hybrid: HR questions are often short ("late", "half day", "sick leave"). Dense embeddings of
very short queries are noisy, so each chunk is scored as

    score = (1 - LEXICAL_WEIGHT) * max(cosine, 0) + LEXICAL_WEIGHT * lexical_overlap

where lexical_overlap = IDF-weighted fraction of the query's content words (stop words removed,
light plural/-ing stemming) that occur in the chunk's text/section/title. IDF is computed over the
indexed chunks, so a rare/unknown term ("maternity") outweighs a ubiquitous one ("leave"): a
question about something the library never mentions does not pass on common words alone.
Chunks below AI_RAG_MIN_SCORE are
dropped, and only chunks within RELATIVE_CUTOFF of the best score are kept (so weakly related
chunks are not sent to the LLM just because top_k allows it). Ordering is deterministic:
score desc, then policy_id, then chunk_index.
"""

import math
import re

from app.services.ai.embeddings import EmbeddingProvider, cosine_similarity
from app.services.ai.rag.models import RetrievedChunk
from app.services.ai.rag.vector_store import PolicyVectorStore

LEXICAL_WEIGHT = 0.5
RELATIVE_CUTOFF = 0.6

_WORD_RE = re.compile(r"[a-z0-9]+")
STOP_WORDS = frozenset(
    """a an the i im i'm me my mine we our you your he she they them it its this that these those there
    is are am was were be been being do does did doing done can could will would shall should may might must
    to of in on at by for from with about into over under as than then so if or and but not no yes
    how what when where which who whom why whose any some all each per many much more most very
    get got take taking have has had happen happens happened tell please know want need allowed allow
    policy policies rule rules company novaworks cb nest employee employees staff""".split()
)


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def content_terms(text: str) -> set[str]:
    terms = set()
    for word in _WORD_RE.findall(text.lower()):
        if word in STOP_WORDS:
            continue
        word = _stem(word)
        if word not in STOP_WORDS:
            terms.add(word)
    return terms


def inverse_document_frequency(term_sets: list[set[str]]) -> dict[str, float]:
    total = len(term_sets)
    frequency: dict[str, int] = {}
    for terms in term_sets:
        for term in terms:
            frequency[term] = frequency.get(term, 0) + 1
    return {term: math.log((total + 1) / (count + 1)) + 1.0 for term, count in frequency.items()}


def lexical_overlap(query_terms: set[str], chunk_terms: set[str], idf: dict[str, float], unseen_idf: float) -> float:
    if not query_terms:
        return 0.0
    weights = {term: idf.get(term, unseen_idf) for term in query_terms}
    return sum(weight for term, weight in weights.items() if term in chunk_terms) / sum(weights.values())


class PolicyRetriever:
    def __init__(self, store: PolicyVectorStore, embedder: EmbeddingProvider, *, top_k: int, min_score: float):
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._min_score = min_score

    async def retrieve(self, question: str) -> list[RetrievedChunk]:
        candidates = await self._store.all_chunks()
        if not candidates:
            return []
        query_vector = await self._embedder.embed_query(question)
        query_terms = content_terms(question)
        chunk_terms = [
            content_terms(f"{s.chunk.policy_title} {s.chunk.section} {s.chunk.text}") for s in candidates
        ]
        idf = inverse_document_frequency(chunk_terms)
        unseen_idf = math.log(len(candidates) + 1) + 1.0  # a term no chunk contains (df = 0)

        scored: list[RetrievedChunk] = []
        for stored, terms in zip(candidates, chunk_terms):
            if len(stored.embedding) != len(query_vector):
                continue
            chunk = stored.chunk
            semantic = cosine_similarity(query_vector, stored.embedding)
            lexical = lexical_overlap(query_terms, terms, idf, unseen_idf)
            score = (1 - LEXICAL_WEIGHT) * max(semantic, 0.0) + LEXICAL_WEIGHT * lexical
            scored.append(RetrievedChunk(chunk=chunk, score=score, semantic_score=semantic, lexical_score=lexical))

        scored.sort(key=lambda item: (-item.score, item.chunk.policy_id, item.chunk.chunk_index))
        if not scored or scored[0].score < self._min_score:
            return []
        floor = max(self._min_score, scored[0].score * RELATIVE_CUTOFF)
        return [item for item in scored if item.score >= floor][: self._top_k]
