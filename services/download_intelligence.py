"""SQL-aware download planning primitives.

This module is intentionally pure: it makes decisions from a SQL inventory
snapshot and can be unit-tested without API clients or Streamlit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

# Lower number means more urgent. Keep these categories aligned with the
# application policy: current match data before historical enrichment.
RESOURCE_PRIORITY = {
    "fixture-prediction": 10,
    "fixture-detail": 20,
    "fixture-lineup": 25,
    "fixture-players": 40,
    "core": 50,
    "season-players": 70,
    "fixture-statistics": 80,
}


@dataclass(frozen=True)
class DownloadTask:
    resource_key: str
    resource_type: str
    missing: int
    deferred: int = 0
    priority: int | None = None
    urgency: int = 0
    estimated_api_cost: int | None = None
    reason: str = ""


def task_from_resource_row(row: dict[str, Any]) -> DownloadTask:
    key = str(row.get("key") or "")
    missing = max(0, int(row.get("missing") or 0))
    deferred = min(missing, max(0, int(row.get("deferred") or 0)))
    priority = RESOURCE_PRIORITY.get(key, 100)
    urgency = int(row.get("urgency") or 0)
    cost = row.get("estimated_api_cost")
    cost = None if cost is None else max(0, int(cost))
    if missing <= deferred:
        reason = "Aucun élément manquant téléchargeable"
    elif deferred:
        reason = f"{missing - deferred} élément(s) requis; {deferred} différé(s)"
    else:
        reason = f"{missing} élément(s) manquant(s)"
    return DownloadTask(
        resource_key=key,
        resource_type=str(row.get("endpoint") or key),
        missing=missing,
        deferred=deferred,
        priority=priority,
        urgency=urgency,
        estimated_api_cost=cost,
        reason=reason,
    )


def order_download_tasks(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank actionable resources; stable tie-breaks avoid oscillating plans."""
    tasks = [task_from_resource_row(row) for row in rows]
    actionable = [task for task in tasks if task.missing > task.deferred]
    actionable.sort(
        key=lambda task: (
            task.priority if task.priority is not None else 100,
            -task.urgency,
            task.estimated_api_cost if task.estimated_api_cost is not None else 10**9,
            task.resource_key,
        )
    )
    return [
        {
            "key": task.resource_key,
            "priority": task.priority,
            "urgency": task.urgency,
            "missing_actionable": task.missing - task.deferred,
            "estimated_api_cost": task.estimated_api_cost,
            "reason": task.reason,
        }
        for task in actionable
    ]


def order_season_player_scopes(
    scopes: Iterable[dict[str, Any]], *, recent_seasons: Iterable[int] = ()
) -> list[dict[str, Any]]:
    """Order season-player backfill from SQL inventory, prioritizing recent gaps.

    Empty scopes come first within the recent-season tier, then larger match
    scopes (more coverage per dispatch decision), with stable season/league ties.
    The function does not mutate its input or decide whether a scope is eligible.
    """
    recent = {int(season) for season in recent_seasons}
    return sorted(
        (dict(scope) for scope in scopes),
        key=lambda scope: (
            0 if int(scope.get("season") or 0) in recent else 1,
            0 if int(scope.get("player_count") or 0) <= 0 else 1,
            -max(0, int(scope.get("match_count") or 0)),
            -int(scope.get("season") or 0),
            int(scope.get("league_id") or 0),
        ),
    )


def annotate_resource_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add transparent priority/action fields without changing coverage counts."""
    order = order_download_tasks(rows)
    rank = {item["key"]: index + 1 for index, item in enumerate(order)}
    actionable = {item["key"]: item for item in order}
    annotated = []
    for original in rows:
        row = dict(original)
        task = actionable.get(row.get("key"))
        row["priority"] = RESOURCE_PRIORITY.get(str(row.get("key")), 100)
        row["actionable_missing"] = max(
            0, int(row.get("missing") or 0) - int(row.get("deferred") or 0)
        )
        row["plan_rank"] = rank.get(row.get("key"))
        row["recommended"] = task is not None and row["plan_rank"] == 1
        row["decision"] = (
            task["reason"] if task else "Couverture suffisante ou éléments différés"
        )
        annotated.append(row)
    return annotated

def order_dispatch_phases(
    plan: dict[str, Any] | None,
    phase_keys: Iterable[str],
    *,
    fallback_order: Iterable[str],
) -> list[str]:
    """Order real dispatcher phases by the SQL-aware plan, with stable fallback.

    The plan may omit non-actionable resources; those phases remain available
    for final SQL/cache checks but are placed after ranked actionable phases.
    """
    available = list(dict.fromkeys(str(key) for key in phase_keys))
    fallback = list(dict.fromkeys(str(key) for key in fallback_order))
    fallback_rank = {key: index for index, key in enumerate(fallback)}
    plan_rank: dict[str, int] = {}
    for index, item in enumerate((plan or {}).get("priority_order") or []):
        if not isinstance(item, dict) or not item.get("key"):
            continue
        key = str(item["key"])
        try:
            rank = int(item.get("priority"))
        except (TypeError, ValueError):
            rank = index
        plan_rank[key] = rank

    return sorted(
        available,
        key=lambda key: (
            plan_rank.get(key, 10**6 + fallback_rank.get(key, len(fallback))),
            fallback_rank.get(key, len(fallback)),
        ),
    )
