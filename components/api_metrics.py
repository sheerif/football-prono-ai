"""Defensive formatting for process-local API cache/quota metrics."""

from collections.abc import Mapping

METRIC_DEFAULTS = {
    "requests_avoided": 0,
    "cache_hits": 0,
    "cache_entries": 0,
    "quota_status_checks": 0,
    "quota_policy_blocks": 0,
    "quota_remote_shared_tokens": 0,
    "quota_remote_block_denials": 0,
    "quota_remote_fallbacks": 0,
}


def normalize_metrics_snapshot(snapshot) -> dict:
    """Return a plain, complete metrics dictionary even for stale/malformed snapshots.

    Convert mapping subclasses to a plain dict so custom get methods cannot
    raise KeyError while the sidebar is rendering. Invalid snapshots are
    intentionally treated as unavailable metrics rather than page failures.
    """
    try:
        raw = dict(snapshot) if isinstance(snapshot, Mapping) else {}
    except Exception:
        raw = {}

    normalized = dict(METRIC_DEFAULTS)
    for key in METRIC_DEFAULTS:
        try:
            normalized[key] = raw.get(key, 0)
        except Exception:
            normalized[key] = 0
    return normalized


def format_api_metrics_captions(snapshot) -> tuple[str, str, str]:
    """Build sidebar captions from a normalized snapshot; never raise KeyError."""
    metrics = normalize_metrics_snapshot(snapshot)
    return (
        f"Cache API · {metrics['requests_avoided']} requêtes data évitées "
        f"({metrics['cache_hits']} hits / {metrics['cache_entries']} entrées)",
        f"Quota · {metrics['quota_status_checks']} préflights / "
        f"{metrics['quota_policy_blocks']} blocages · métriques locales au processus",
        f"Quota partagé · {metrics['quota_remote_shared_tokens']} tokens réservés / "
        f"{metrics['quota_remote_block_denials']} refus · "
        f"{metrics['quota_remote_fallbacks']} fallback(s) local(aux)",
    )
