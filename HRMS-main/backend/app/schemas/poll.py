from pydantic import BaseModel, Field


class PollCreate(BaseModel):
    question: str = Field(min_length=5, max_length=300)
    options: list[str] = Field(min_length=2, max_length=6)
    audience: str | None = Field(default=None, max_length=10)  # ORG | TEAM; the team is always the creator's own


class PollVote(BaseModel):
    option_index: int = Field(ge=0)
