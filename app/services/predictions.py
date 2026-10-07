"""Prediction-service transport and mapping into the public prediction contract."""

import logging
import math
from datetime import date
from typing import Literal

import httpx2
from pydantic import BaseModel, Field, ValidationError

from app.constants import PredictionFallbackReason
from app.core.config import settings
from app.schemas.predictions import (
    ModelPredictionSource,
    ModelPredictionWarning,
    PredictionCoverage,
    PredictionHistory,
    PredictionWarning,
    Probability,
    UnknownPredictionWarning,
    WinProbabilities,
)

logger = logging.getLogger(__name__)


class _TeamProbability(BaseModel):
    team_id: str = Field(strict=True, min_length=1)
    probability: Probability


class _UpstreamWarning(BaseModel):
    code: str = Field(strict=True, min_length=1)
    message: str | None = None


class _ModelResponse(BaseModel):
    task: Literal["match_win"]
    model_version: str = Field(strict=True, min_length=1)
    history: PredictionHistory
    coverage: PredictionCoverage
    team_probabilities: list[_TeamProbability] = Field(min_length=2, max_length=2)
    warnings: list[_UpstreamWarning] = []

    def prediction(self, team_a_id: str, team_b_id: str) -> WinProbabilities:
        """Validate the requested pair and translate model warnings.

        :param team_a_id: First requested team's ID.
        :param team_b_id: Second requested team's ID.
        :return: Model probabilities with evidence and typed warnings.
        :raises ValueError: If the probabilities do not describe the requested pair.
        """
        probabilities = {row.team_id: row.probability for row in self.team_probabilities}
        if set(probabilities) != {team_a_id, team_b_id}:
            raise ValueError("Prediction teams do not match the requested pair")
        if not math.isclose(sum(probabilities.values()), 1.0, rel_tol=0, abs_tol=1e-10):
            raise ValueError("Prediction probabilities do not sum to one")
        warnings: list[PredictionWarning] = []
        for warning in self.warnings:
            try:
                warnings.append(ModelPredictionWarning.model_validate({"code": warning.code}))
            except ValidationError:
                warnings.append(UnknownPredictionWarning(upstream_code=warning.code, message=warning.message))
        return WinProbabilities(
            team_a=probabilities[team_a_id],
            team_b=probabilities[team_b_id],
            source=ModelPredictionSource(
                model_version=self.model_version,
                history=self.history,
                coverage=self.coverage,
            ),
            warnings=warnings,
        )


async def predict_match(team_a_id: str, team_b_id: str, as_of: date) -> WinProbabilities | PredictionFallbackReason:
    """Request a model prediction or identify why the caller should use Elo.

    :param team_a_id: First team's ID.
    :param team_b_id: Second team's ID.
    :param as_of: Prediction date.
    :return: Model probabilities or an explicit fallback reason.
    """
    url = settings.PREDICTION_SERVICE_URL
    if not url:
        return PredictionFallbackReason.MODEL_NOT_CONFIGURED
    try:
        if url.startswith(("unix://", "/")) or url.endswith(".sock"):
            transport = httpx2.AsyncHTTPTransport(uds=url.removeprefix("unix://"))
            target = "http://localhost/predict"
        else:
            transport = None
            target = f"{url.rstrip('/')}/predict"
        async with httpx2.AsyncClient(transport=transport, timeout=2.0) as client:
            response = await client.post(
                target,
                json={"task": "match_win", "as_of": as_of.isoformat(), "team_a_id": team_a_id, "team_b_id": team_b_id},
            )
            response.raise_for_status()
        return _ModelResponse.model_validate(response.json()).prediction(team_a_id, team_b_id)
    except httpx2.TimeoutException as error:
        return _fallback(PredictionFallbackReason.MODEL_TIMEOUT, error)
    except httpx2.HTTPStatusError as error:
        return _fallback(PredictionFallbackReason.MODEL_HTTP_ERROR, error)
    except (httpx2.HTTPError, httpx2.InvalidURL) as error:
        return _fallback(PredictionFallbackReason.MODEL_UNAVAILABLE, error)
    except ValueError as error:  # pydantic.ValidationError and malformed JSON both subclass ValueError
        return _fallback(PredictionFallbackReason.MODEL_INVALID_RESPONSE, error)


def _fallback(reason: PredictionFallbackReason, error: Exception) -> PredictionFallbackReason:
    """Log a prediction-service failure and the Elo fallback it forces.

    :param reason: Why the model probability is unavailable.
    :param error: Failure raised while talking to the prediction service.
    :return: ``reason``, so a handler can return the helper's result directly.
    """
    logger.warning("prediction service failed; falling back to Elo (%s)", reason, exc_info=error)
    return reason
