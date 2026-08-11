"""Contrat explicite des sources externes de prédiction."""

from __future__ import annotations

from collections.abc import Mapping

from services import probability_validation

AVAILABLE, NO_SIGNAL, INVALID, ERROR = "AVAILABLE", "NO_SIGNAL", "INVALID", "ERROR"
MARKETS = {"1", "N", "2", "1X", "X2", "12", "PRUDENCE", "NO BET"}


def describe(name: str, payload: Mapping[str, object] | None) -> dict:
    """Ne confond jamais absence, données invalides et signal utilisable."""
    result = {"name": name, "status": NO_SIGNAL, "distribution": None, "market": None, "reliability": None}
    if payload is None or payload == {}:
        return result
    if not isinstance(payload, Mapping):
        return {**result, "status": INVALID}
    explicit = str(payload.get("status") or "").upper()
    if explicit in {NO_SIGNAL, INVALID, ERROR}:
        return {**result, "status": explicit}
    raw = [payload.get(key) for key in probability_validation.KEYS]
    if any(value is not None for value in raw):
        try:
            result["distribution"] = probability_validation.validate_distribution(payload)
        except ValueError:
            return {**result, "status": INVALID}
    market = str(payload.get("market") or payload.get("advice") or "").upper().strip()
    if market in MARKETS:
        result["market"] = market
    if result["distribution"] is not None or result["market"] is not None:
        result["status"] = AVAILABLE
    try:
        result["reliability"] = max(0.0, min(1.0, float(payload.get("reliability", 0.7))))
    except (TypeError, ValueError):
        result["reliability"] = 0.7
    return result
