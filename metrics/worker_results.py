#!/usr/bin/env python3
"""Worker result JSON analytics for CENTRAL.

Parses worker result files from state/central_runtime/.worker-results/ and
aggregates quality metrics: discovery density, blocker frequency, validation
pass rates, completion ratios, files-changed stats, artifact production rates,
requirements coverage, and system_fit_assessment distributions.

All public analysis functions accept a list of result dicts as returned by
``load_results()``.  For grouping by ``model`` or ``task_type``, first enrich
the list with ``correlate_with_db()`` so those DB-sourced fields are available.

Typical usage::

    from pathlib import Path
    import sqlite3
    from metrics.worker_results import load_results, correlate_with_db, discovery_density

    results_dir = Path("state/central_runtime/.worker-results")
    results = load_results(results_dir)

    # Optional: enrich with DB metadata for model / task_type grouping
    conn = sqlite3.connect("state/central_tasks.db")
    enriched = correlate_with_db(results, conn)

    print(discovery_density(enriched, group_by="model"))
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float | None:
    """Return the *pct*-th percentile (0–100) of *values*, or None if empty."""
    if not values:
        return None
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _safe_list(val: Any) -> list:
    return val if isinstance(val, list) else []


def _safe_str(val: Any) -> str:
    return val if isinstance(val, str) else ""


def _group_key(result: dict[str, Any], group_by: str) -> str:
    """Extract the grouping dimension from a (possibly DB-enriched) result."""
    if group_by == "model":
        return _safe_str(result.get("_model")) or "unknown"
    if group_by == "task_type":
        return _safe_str(result.get("_task_type")) or "unknown"
    if group_by == "status":
        return _safe_str(result.get("status")) or "unknown"
    return "all"


_VALID_GROUP_BY = {"model", "task_type", "status", "none"}


def _check_group_by(group_by: str) -> None:
    if group_by not in _VALID_GROUP_BY:
        raise ValueError(f"group_by must be one of {_VALID_GROUP_BY}, got {group_by!r}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_results(
    results_dir: Path | str,
    *,
    latest_only: bool = True,
) -> list[dict[str, Any]]:
    """Load worker result JSON files from *results_dir*.

    Expected layout::

        results_dir/
            {TASK_ID}/
                {RUN_ID}.json   # one or more per task

    Args:
        results_dir: Path to the ``.worker-results`` directory.
        latest_only: When True (default), load only the alphabetically last
            JSON file per task directory (timestamps are embedded in the
            filename, so last == most recent).  When False, load all files.

    Returns:
        List of parsed result dicts.  Each dict gains a ``_source_path`` key
        pointing to the file it was loaded from.  Malformed or unreadable
        files are silently skipped.
    """
    results_dir = Path(results_dir)
    if not results_dir.exists():
        return []

    results: list[dict[str, Any]] = []
    for task_dir in sorted(results_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        json_files = sorted(task_dir.glob("*.json"))
        if not json_files:
            continue
        to_load = [json_files[-1]] if latest_only else json_files
        for json_file in to_load:
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, dict):
                data["_source_path"] = str(json_file)
                results.append(data)

    return results


# ---------------------------------------------------------------------------
# DB enrichment
# ---------------------------------------------------------------------------

def correlate_with_db(
    results: list[dict[str, Any]],
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Enrich worker results with DB metadata for richer grouping.

    Looks up each result's ``task_id`` in the CENTRAL task DB and adds the
    following underscore-prefixed keys (to avoid collisions with JSON fields):

    * ``_model`` — ``task_runtime_state.effective_worker_model``
    * ``_task_type`` — ``tasks.task_type``
    * ``_worker_effort`` — extracted from
      ``task_execution_settings.execution_metadata_json``
    * ``_target_repo_id`` — ``tasks.target_repo_id``
    * ``_initiative`` — ``tasks.initiative``

    Results whose ``task_id`` has no DB record receive ``"unknown"`` for each
    field.  The input list is not modified in place; a new list is returned.

    Args:
        results: List of result dicts from ``load_results()``.
        conn: Open ``sqlite3.Connection`` to the CENTRAL task DB.

    Returns:
        New list of dicts with DB fields merged in.
    """
    sql = """
        SELECT
            t.task_id,
            t.task_type,
            t.target_repo_id,
            t.initiative,
            trs.effective_worker_model,
            tes.execution_metadata_json
        FROM tasks t
        LEFT JOIN task_runtime_state trs ON trs.task_id = t.task_id
        LEFT JOIN task_execution_settings tes ON tes.task_id = t.task_id
    """
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    db_map: dict[str, dict[str, Any]] = {}
    for row in cur.fetchall():
        record = dict(zip(cols, row))
        tid = record["task_id"]
        try:
            meta = json.loads(record.get("execution_metadata_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        db_map[tid] = {
            "_model": record.get("effective_worker_model") or "unknown",
            "_task_type": record.get("task_type") or "unknown",
            "_worker_effort": meta.get("worker_effort") or "unknown",
            "_target_repo_id": record.get("target_repo_id") or "unknown",
            "_initiative": record.get("initiative") or "unknown",
        }

    enriched = []
    for r in results:
        task_id = _safe_str(r.get("task_id"))
        db_fields = db_map.get(task_id, {
            "_model": "unknown",
            "_task_type": "unknown",
            "_worker_effort": "unknown",
            "_target_repo_id": "unknown",
            "_initiative": "unknown",
        })
        enriched.append({**r, **db_fields})

    return enriched


# ---------------------------------------------------------------------------
# 1. Discovery density
# ---------------------------------------------------------------------------

def discovery_density(
    results: list[dict[str, Any]],
    *,
    group_by: str = "model",
) -> list[dict[str, Any]]:
    """Average number of discoveries per task, grouped by *group_by*.

    Uses the ``discoveries`` array in each worker result.

    Args:
        results: Result dicts from ``load_results()`` or ``correlate_with_db()``.
        group_by: One of ``"model"``, ``"task_type"``, ``"status"``,
            or ``"none"`` for an overall aggregate.

    Returns:
        List of dicts with keys:
            {group_by}, task_count, total_discoveries,
            avg_discoveries, p50_discoveries, p90_discoveries.
    """
    _check_group_by(group_by)
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in results:
        key = _group_key(r, group_by)
        buckets[key].append(len(_safe_list(r.get("discoveries"))))

    dim = group_by if group_by != "none" else "group"
    rows = []
    for key, counts in sorted(buckets.items()):
        fv = [float(c) for c in counts]
        rows.append({
            dim: key,
            "task_count": len(counts),
            "total_discoveries": sum(counts),
            "avg_discoveries": round(sum(counts) / len(counts), 3) if counts else None,
            "p50_discoveries": _percentile(fv, 50),
            "p90_discoveries": _percentile(fv, 90),
        })
    return rows


# ---------------------------------------------------------------------------
# 2. Blocker frequency
# ---------------------------------------------------------------------------

def blocker_frequency(
    results: list[dict[str, Any]],
    *,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """Categorized blocker text grouped by normalised prefix.

    Normalisation mirrors ``failure_mode_groups`` in ``query.py``: lower-case,
    trimmed, truncated to 120 chars, hashes and long numbers collapsed.

    Returns:
        List of dicts sorted descending by count (up to *top_n*), with keys:
            blocker_prefix, count, example_task_ids (up to 3).
    """
    def _normalise(text: str) -> str:
        text = text.strip().lower()[:120]
        text = re.sub(r"[\s,.:;!?]+$", "", text)
        text = re.sub(r"\b[0-9a-f]{8,}\b", "<hash>", text)
        text = re.sub(r"\b\d{4,}\b", "<n>", text)
        return text

    groups: dict[str, list[str]] = defaultdict(list)
    for r in results:
        task_id = _safe_str(r.get("task_id")) or _safe_str(r.get("_source_path"))
        for b in _safe_list(r.get("blockers")):
            if not isinstance(b, str) or not b.strip():
                continue
            key = _normalise(b)
            groups[key].append(task_id)

    rows = []
    for prefix, task_ids in sorted(groups.items(), key=lambda x: -len(x[1])):
        rows.append({
            "blocker_prefix": prefix,
            "count": len(task_ids),
            "example_task_ids": list(dict.fromkeys(task_ids))[:3],
        })
    return rows[:top_n]


def blocker_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall blocker rate across all results.

    Returns:
        Dict with keys:
            total_tasks, tasks_with_blockers, blocker_rate,
            total_blocker_mentions.
    """
    total = len(results)
    with_blockers = sum(
        1 for r in results if any(
            isinstance(b, str) and b.strip()
            for b in _safe_list(r.get("blockers"))
        )
    )
    total_mentions = sum(
        sum(1 for b in _safe_list(r.get("blockers")) if isinstance(b, str) and b.strip())
        for r in results
    )
    return {
        "total_tasks": total,
        "tasks_with_blockers": with_blockers,
        "blocker_rate": round(with_blockers / total, 4) if total else None,
        "total_blocker_mentions": total_mentions,
    }


# ---------------------------------------------------------------------------
# 3. Validation pass rates
# ---------------------------------------------------------------------------

def validation_pass_rates(
    results: list[dict[str, Any]],
    *,
    min_sample: int = 1,
) -> list[dict[str, Any]]:
    """Per-check validation pass rates aggregated across all results.

    Each entry in ``result["validation"]`` is ``{name, passed, notes}``.  This
    function groups by normalised check name and computes pass rate.

    Args:
        results: Result dicts.
        min_sample: Exclude checks with fewer than *min_sample* observations.

    Returns:
        List of dicts sorted descending by sample_size, with keys:
            check_name, sample_size, passed, failed, pass_rate.
    """
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0})
    for r in results:
        for check in _safe_list(r.get("validation")):
            if not isinstance(check, dict):
                continue
            name = _safe_str(check.get("name")).strip()
            if not name:
                continue
            if check.get("passed") is True:
                buckets[name]["passed"] += 1
            else:
                buckets[name]["failed"] += 1

    rows = []
    for name, counts in buckets.items():
        total = counts["passed"] + counts["failed"]
        if total < min_sample:
            continue
        rows.append({
            "check_name": name,
            "sample_size": total,
            "passed": counts["passed"],
            "failed": counts["failed"],
            "pass_rate": round(counts["passed"] / total, 4) if total else None,
        })
    rows.sort(key=lambda r: -r["sample_size"])
    return rows


# ---------------------------------------------------------------------------
# 4. Completion ratios
# ---------------------------------------------------------------------------

def completion_ratios(
    results: list[dict[str, Any]],
    *,
    group_by: str = "model",
) -> list[dict[str, Any]]:
    """Completion ratio (completed / (completed + remaining)) per task, grouped.

    A task with zero completed and zero remaining items yields ``None`` (no
    checklist) and is excluded from aggregate statistics.

    Args:
        results: Result dicts.
        group_by: One of ``"model"``, ``"task_type"``, ``"status"``, ``"none"``.

    Returns:
        List of dicts with keys:
            {group_by}, task_count, fully_complete, partial, no_checklist,
            avg_completion_ratio, p50_completion_ratio.
    """
    _check_group_by(group_by)
    dim = group_by if group_by != "none" else "group"

    Bucket = dict  # type alias for readability
    buckets: dict[str, Bucket] = defaultdict(lambda: {
        "ratios": [], "fully_complete": 0, "partial": 0, "no_checklist": 0,
    })

    for r in results:
        key = _group_key(r, group_by)
        done = len(_safe_list(r.get("completed_items")))
        remaining = len(_safe_list(r.get("remaining_items")))
        total = done + remaining
        if total == 0:
            buckets[key]["no_checklist"] += 1
        else:
            ratio = done / total
            buckets[key]["ratios"].append(ratio)
            if remaining == 0:
                buckets[key]["fully_complete"] += 1
            else:
                buckets[key]["partial"] += 1

    rows = []
    for key, b in sorted(buckets.items()):
        ratios = b["ratios"]
        rows.append({
            dim: key,
            "task_count": b["fully_complete"] + b["partial"] + b["no_checklist"],
            "fully_complete": b["fully_complete"],
            "partial": b["partial"],
            "no_checklist": b["no_checklist"],
            "avg_completion_ratio": round(sum(ratios) / len(ratios), 4) if ratios else None,
            "p50_completion_ratio": _percentile(ratios, 50),
        })
    return rows


# ---------------------------------------------------------------------------
# 5. Files-changed stats
# ---------------------------------------------------------------------------

def files_changed_stats(
    results: list[dict[str, Any]],
    *,
    group_by: str = "model",
) -> list[dict[str, Any]]:
    """Distribution of file-change volume per task, grouped by *group_by*.

    Counts the number of entries in each result's ``files_changed`` array.

    Returns:
        List of dicts with keys:
            {group_by}, task_count, total_files_changed,
            avg_files_changed, p50_files_changed, p90_files_changed,
            p99_files_changed, zero_files_tasks.
    """
    _check_group_by(group_by)
    dim = group_by if group_by != "none" else "group"
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in results:
        key = _group_key(r, group_by)
        buckets[key].append(len(_safe_list(r.get("files_changed"))))

    rows = []
    for key, counts in sorted(buckets.items()):
        fv = [float(c) for c in counts]
        rows.append({
            dim: key,
            "task_count": len(counts),
            "total_files_changed": sum(counts),
            "avg_files_changed": round(sum(counts) / len(counts), 2) if counts else None,
            "p50_files_changed": _percentile(fv, 50),
            "p90_files_changed": _percentile(fv, 90),
            "p99_files_changed": _percentile(fv, 99),
            "zero_files_tasks": sum(1 for c in counts if c == 0),
        })
    return rows


# ---------------------------------------------------------------------------
# 6. Artifact production rates
# ---------------------------------------------------------------------------

def artifact_production_rates(
    results: list[dict[str, Any]],
    *,
    group_by: str = "model",
) -> list[dict[str, Any]]:
    """Rate at which tasks produce at least one artifact, grouped by *group_by*.

    An artifact is any non-empty entry in the ``artifacts`` array (string or
    dict both count).

    Returns:
        List of dicts with keys:
            {group_by}, task_count, tasks_with_artifacts, artifact_rate,
            total_artifacts, avg_artifacts_per_task.
    """
    _check_group_by(group_by)
    dim = group_by if group_by != "none" else "group"

    Bucket = dict
    buckets: dict[str, Bucket] = defaultdict(lambda: {
        "task_count": 0, "tasks_with_artifacts": 0, "total_artifacts": 0,
    })

    for r in results:
        key = _group_key(r, group_by)
        artifacts = _safe_list(r.get("artifacts"))
        # Filter out empty/falsy entries
        non_empty = [a for a in artifacts if a]
        b = buckets[key]
        b["task_count"] += 1
        b["total_artifacts"] += len(non_empty)
        if non_empty:
            b["tasks_with_artifacts"] += 1

    rows = []
    for key, b in sorted(buckets.items()):
        tc = b["task_count"]
        tw = b["tasks_with_artifacts"]
        ta = b["total_artifacts"]
        rows.append({
            dim: key,
            "task_count": tc,
            "tasks_with_artifacts": tw,
            "artifact_rate": round(tw / tc, 4) if tc else None,
            "total_artifacts": ta,
            "avg_artifacts_per_task": round(ta / tc, 3) if tc else None,
        })
    return rows


# ---------------------------------------------------------------------------
# 7. Requirements coverage
# ---------------------------------------------------------------------------

def requirements_coverage(
    results: list[dict[str, Any]],
    *,
    group_by: str = "model",
) -> list[dict[str, Any]]:
    """Requirements assessment verdict aggregation, grouped by *group_by*.

    Aggregates verdicts from ``requirements_assessment[]`` arrays.  Each item
    has a ``verdict`` field of ``"met"``, ``"partially_met"``, ``"not_met"``,
    or ``"not_applicable"``.

    Returns:
        List of dicts with keys:
            {group_by}, tasks_assessed, total_requirements,
            met, partially_met, not_met, not_applicable,
            coverage_rate (met / (met + partially_met + not_met)),
            full_coverage_rate (met / total excluding not_applicable).
    """
    _check_group_by(group_by)
    dim = group_by if group_by != "none" else "group"

    Bucket = dict
    buckets: dict[str, Bucket] = defaultdict(lambda: {
        "tasks_assessed": 0,
        "total_requirements": 0,
        "met": 0, "partially_met": 0, "not_met": 0, "not_applicable": 0,
    })

    for r in results:
        ra = _safe_list(r.get("requirements_assessment"))
        if not ra:
            continue
        key = _group_key(r, group_by)
        b = buckets[key]
        b["tasks_assessed"] += 1
        for item in ra:
            if not isinstance(item, dict):
                continue
            verdict = _safe_str(item.get("verdict")).lower()
            b["total_requirements"] += 1
            if verdict == "met":
                b["met"] += 1
            elif verdict == "partially_met":
                b["partially_met"] += 1
            elif verdict == "not_met":
                b["not_met"] += 1
            elif verdict == "not_applicable":
                b["not_applicable"] += 1

    rows = []
    for key, b in sorted(buckets.items()):
        applicable = b["total_requirements"] - b["not_applicable"]
        denominator = b["met"] + b["partially_met"] + b["not_met"]
        rows.append({
            dim: key,
            "tasks_assessed": b["tasks_assessed"],
            "total_requirements": b["total_requirements"],
            "met": b["met"],
            "partially_met": b["partially_met"],
            "not_met": b["not_met"],
            "not_applicable": b["not_applicable"],
            "coverage_rate": round(b["met"] / denominator, 4) if denominator else None,
            "full_coverage_rate": round(b["met"] / applicable, 4) if applicable else None,
        })
    return rows


# ---------------------------------------------------------------------------
# 8. System fit verdict distribution
# ---------------------------------------------------------------------------

def system_fit_distribution(
    results: list[dict[str, Any]],
    *,
    group_by: str = "model",
) -> list[dict[str, Any]]:
    """Distribution of system_fit_assessment verdicts and risk levels.

    Only results that include a ``system_fit_assessment`` dict are counted.

    Returns:
        List of dicts with keys:
            {group_by}, assessed_tasks,
            fit, partial_fit, not_fit,        (verdict counts)
            risk_low, risk_medium, risk_high   (local_optimization_risk counts).
    """
    _check_group_by(group_by)
    dim = group_by if group_by != "none" else "group"

    Bucket = dict
    buckets: dict[str, Bucket] = defaultdict(lambda: {
        "assessed_tasks": 0,
        "fit": 0, "partial_fit": 0, "not_fit": 0,
        "risk_low": 0, "risk_medium": 0, "risk_high": 0,
    })

    for r in results:
        sfa = r.get("system_fit_assessment")
        if not isinstance(sfa, dict):
            continue
        key = _group_key(r, group_by)
        b = buckets[key]
        b["assessed_tasks"] += 1

        verdict = _safe_str(sfa.get("verdict")).lower().replace("-", "_")
        if verdict in ("fit",):
            b["fit"] += 1
        elif verdict in ("partial_fit",):
            b["partial_fit"] += 1
        elif verdict in ("not_fit",):
            b["not_fit"] += 1

        risk = _safe_str(sfa.get("local_optimization_risk")).lower()
        if risk == "low":
            b["risk_low"] += 1
        elif risk == "medium":
            b["risk_medium"] += 1
        elif risk == "high":
            b["risk_high"] += 1

    rows = []
    for key, b in sorted(buckets.items()):
        assessed = b["assessed_tasks"]
        rows.append({
            dim: key,
            "assessed_tasks": assessed,
            "fit": b["fit"],
            "partial_fit": b["partial_fit"],
            "not_fit": b["not_fit"],
            "fit_rate": round(b["fit"] / assessed, 4) if assessed else None,
            "risk_low": b["risk_low"],
            "risk_medium": b["risk_medium"],
            "risk_high": b["risk_high"],
        })
    return rows


# ---------------------------------------------------------------------------
# 9. Audit verdict distribution (the primary quality signal)
# ---------------------------------------------------------------------------

def audit_verdict_distribution(
    results: list[dict[str, Any]],
    *,
    group_by: str = "model",
) -> list[dict[str, Any]]:
    """Aggregate audit verdicts from AUDIT task result files.

    This is the primary quality signal: an 'accepted' verdict means the impl
    passed audit; 'rework_required' means it was sent back.

    Only results whose task_id ends with '-AUDIT' are included.  Pass a list
    enriched via ``correlate_with_db()`` to enable grouping by model.

    Args:
        results: Result dicts from ``load_results()`` or ``correlate_with_db()``.
        group_by: One of ``"model"``, ``"task_type"``, ``"status"``, ``"none"``.

    Returns:
        List of dicts with keys:
            {group_by}, total_audits, accepted, rework_required, other,
            acceptance_rate, rework_rate.
        Sorted descending by total_audits.
    """
    _check_group_by(group_by)
    dim = group_by if group_by != "none" else "group"

    buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "accepted": 0, "rework_required": 0, "other": 0}
    )

    for r in results:
        task_id = _safe_str(r.get("task_id"))
        if not task_id.endswith("-AUDIT"):
            continue
        key = _group_key(r, group_by)
        verdict = _safe_str(r.get("verdict")).lower().strip()
        b = buckets[key]
        b["total"] += 1
        if verdict == "accepted":
            b["accepted"] += 1
        elif verdict == "rework_required":
            b["rework_required"] += 1
        else:
            b["other"] += 1

    rows = []
    for key, b in sorted(buckets.items(), key=lambda x: -x[1]["total"]):
        total = b["total"]
        rows.append({
            dim: key,
            "total_audits": total,
            "accepted": b["accepted"],
            "rework_required": b["rework_required"],
            "other": b["other"],
            "acceptance_rate": round(b["accepted"] / total, 4) if total else None,
            "rework_rate": round(b["rework_required"] / total, 4) if total else None,
        })
    return rows
