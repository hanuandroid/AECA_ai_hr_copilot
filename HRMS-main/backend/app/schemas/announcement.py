from datetime import datetime

from pydantic import BaseModel, Field


class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    body: str = Field(min_length=5, max_length=5000)
    audience: str | None = Field(default=None, max_length=10)  # ORG | TEAM; the team is always the creator's own


class AnnouncementRead(BaseModel):
    id: int
    title: str
    body: str
    author_id: int
    author_name: str
    audience: str
    created_at: datetime
