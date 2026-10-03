from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    """Normalised text of one hr_policies row (in CB Nest each row is exactly one document)."""

    policy_id: int
    title: str
    category: str
    source_filename: str
    text: str
    checksum: str  # sha256 of normalised text


@dataclass(frozen=True, slots=True)
class PolicyChunkData:
    chunk_id: str
    policy_id: int
    policy_title: str
    category: str
    source_filename: str
    section: str
    chunk_index: int
    text: str
    index_key: str

    def embedding_text(self) -> str:
        """Heading context is embedded with the body so short sections stay findable."""
        return f"{self.policy_title} - {self.section}\n{self.text}"


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: PolicyChunkData
    score: float  # hybrid score used for ranking/threshold
    semantic_score: float
    lexical_score: float


# --- LLM structured output --------------------------------------------------------


class PolicyLLMOutput(BaseModel):
    """What the model must return. `citations` are the S-labels of the context blocks used."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    grounded: bool
    citations: list[str]


# --- API response -----------------------------------------------------------------


class PolicySource(BaseModel):
    chunk_id: str
    title: str
    category: str
    filename: str
    section: str


class PolicyAnswer(BaseModel):
    answer: str
    grounded: bool
    sources: list[PolicySource] = Field(default_factory=list)
