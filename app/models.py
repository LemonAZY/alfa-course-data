from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Stage = Literal["idea", "launch", "operating", "growth"]
Level = Literal["beginner", "intermediate", "advanced"]
Format = Literal["article", "template", "course", "webinar", "podcast", "glossary", "calculator"]
Event = Literal["view", "started", "completed", "dismissed", "clicked", "purchased"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Material(Input):
    id: str
    title: str = Field(min_length=1)
    summary: str = ""
    format: Format
    topics: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    stages: list[Stage] = Field(default_factory=list)
    level: Level | None = None
    access: Literal["free", "paid", "unknown"] = "unknown"
    url: HttpUrl | None = None
    related: list[str] = Field(default_factory=list)
    popularity: int = Field(default=0, ge=0)


class Interaction(Input):
    material_id: str = Field(min_length=1, max_length=100)
    event: Event


class Profile(Input):
    task: str = Field(default="", max_length=300)
    topic: str = Field(default="", max_length=120)
    industry: str = Field(default="", max_length=120)
    stage: Stage | None = None
    level: Level | None = None
    preferred_format: Format | None = None
    history: list[Interaction] = Field(default_factory=list, max_length=100)
    limit: int = Field(default=5, ge=1, le=12)
    strategy: Literal["personalized", "baseline"] = "personalized"


class Recommendation(BaseModel):
    material: Material
    reasons: list[str]
    next_step: str


class Result(BaseModel):
    strategy: str
    mode: Literal["cold_start", "personalized", "fallback"]
    clarification: str | None = None
    recommendations: list[Recommendation]
