"""
Prediction model registry.

Single source of truth for which `build_prediction` callables exist and how
to invoke them by name.  Both the Flask app and the cron scripts import
`MODEL_BUILDERS` / `get_available_models()` / `run_model()` from here so
that adding a future "Model 3" only requires importing it and adding one
entry to ``MODEL_BUILDERS`` below.
"""

from __future__ import annotations

from typing import Callable

from models.model_1 import PredictionError
from models.model_1 import build_prediction as build_prediction_m1
from models.model_2 import build_prediction as build_prediction_m2

# Maps a human-readable model name to the function that produces a
# prediction dict for a single ticker.  Adding "Model 3" later means
# importing it and adding one row here.
MODEL_BUILDERS: dict[str, Callable[..., dict]] = {
    "Model 1": build_prediction_m1,
    "Model 2": build_prediction_m2,
}


def get_available_models() -> list[str]:
    """Return the list of registered model names in stable order."""
    return list(MODEL_BUILDERS.keys())


def run_model(model_name: str, ticker: str) -> dict:
    """Look up the registered builder and run it for *ticker*.

    Falls back to "Model 1" if *model_name* is unknown so the existing
    /predict route behaviour is preserved.
    """
    build_fn = MODEL_BUILDERS.get(model_name, build_prediction_m1)
    return build_fn(ticker)


def return_direction(value: float) -> str:
    """Map a return value to 'up', 'down', or 'neutral'.

    Shared by the Flask routes, the evaluation service, and the monthly
    backtest script so direction logic lives in one place.
    """
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "neutral"


__all__ = [
    "MODEL_BUILDERS",
    "PredictionError",
    "get_available_models",
    "return_direction",
    "run_model",
]
