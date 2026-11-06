"""Pydantic response models — the public API contract."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReasonOut(BaseModel):
    code: str = Field(..., description="Machine code the UI can map to an icon/chip")
    detail: str = Field(..., description="Member-facing text fragment")


class SimilarCircleOut(BaseModel):
    circle_id: int
    name: str


class RecommendationOut(BaseModel):
    circle_id: int
    name: str
    slug: str
    topic: str | None
    format: str
    join_policy: str
    member_count: int
    max_members: int | None
    score: float = Field(..., description="Overall fit, 0..1 (higher is better)")
    why: str = Field(..., description="Short, member-facing explanation")
    reasons: list[ReasonOut] = Field(
        default_factory=list, description="Structured reasons behind `why`"
    )
    similar_circles: list[SimilarCircleOut] = Field(
        default_factory=list,
        description="Near-duplicate circles folded into this one (the most active was kept)",
    )
    signals: dict = Field(
        default_factory=dict,
        description="Transparent per-signal breakdown (debugging / tuning)",
    )


class RecommendationsResponse(BaseModel):
    user_id: int
    strategy: str = Field(..., description="'interest_based' or 'cold_start'")
    count: int
    recommendations: list[RecommendationOut]


class UserSummary(BaseModel):
    id: int
    first_name: str | None
    last_name: str | None
    topic_interests: list[str]
    profile_completeness: float
    member_circle_ids: list[int]
