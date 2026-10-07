"""Typed prediction provenance, model evidence, and warning indicators."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.constants import (
    PredictionFallbackReason,
    PredictionRating,
    PredictionSourceKind,
    PredictionWarningCode,
)

Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Count = Annotated[int, Field(strict=True, ge=0)]
EffectiveCount = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class PredictionHistory(BaseModel):
    """Latest eligible model observation and its age on the prediction date."""

    max_date: date | None
    freshness_days: Count | None


class TeamPredictionCoverage(BaseModel):
    """Raw and time-weighted evidence available for one team."""

    match_results: Count
    effective_match_results: EffectiveCount
    map_results: Count
    effective_map_results: EffectiveCount
    map_name_results: Count
    effective_map_name_results: EffectiveCount
    patch_map_results: Count
    effective_patch_map_results: EffectiveCount
    last_seen: date | None


class PredictionCoverage(BaseModel):
    """Frozen history, team evidence, and head-to-head coverage used by the model."""

    history_matches: Count
    history_max_played_on: date | None
    team_a: TeamPredictionCoverage
    team_b: TeamPredictionCoverage
    head_to_head_results: Count
    patch_scope: str


class ModelPredictionSource(BaseModel):
    """The model that produced a probability and the evidence it used."""

    kind: Literal[PredictionSourceKind.MODEL] = PredictionSourceKind.MODEL
    model_version: str = Field(strict=True, min_length=1)
    history: PredictionHistory
    coverage: PredictionCoverage


class EloPredictionSource(BaseModel):
    """The stored rating used to calculate an Elo probability."""

    kind: Literal[PredictionSourceKind.ELO] = PredictionSourceKind.ELO
    rating: PredictionRating


class ModelPredictionWarning(BaseModel):
    """A known model warning that clients map to their own wording."""

    code: Literal[
        PredictionWarningCode.UNKNOWN_TEAM,
        PredictionWarningCode.LOW_COVERAGE,
        PredictionWarningCode.UNKNOWN_PATCH,
        PredictionWarningCode.NO_ELIGIBLE_HISTORY,
        PredictionWarningCode.NO_TEAM_MAP_HISTORY,
        PredictionWarningCode.LIMITED_TEAM_MAP_HISTORY,
        PredictionWarningCode.MAP_NOT_IN_MODEL,
        PredictionWarningCode.UNVALIDATED_MAP_FALLBACK,
    ]


class EloFallbackWarning(BaseModel):
    """Why the series prediction used Elo instead of the model."""

    code: Literal[PredictionWarningCode.ELO_FALLBACK] = PredictionWarningCode.ELO_FALLBACK
    reason: PredictionFallbackReason


class UnknownPredictionWarning(BaseModel):
    """An upstream warning that remains visible without a known client label."""

    code: Literal[PredictionWarningCode.UNKNOWN] = PredictionWarningCode.UNKNOWN
    upstream_code: str = Field(min_length=1)
    message: str | None = None


PredictionWarning = Annotated[
    ModelPredictionWarning | EloFallbackWarning | UnknownPredictionWarning,
    Field(discriminator="code"),
]


class WinProbabilities(BaseModel):
    """Complementary probabilities with explicit sources and warning indicators."""

    team_a: Probability
    team_b: Probability
    source: Annotated[ModelPredictionSource | EloPredictionSource, Field(discriminator="kind")]
    warnings: list[PredictionWarning]
