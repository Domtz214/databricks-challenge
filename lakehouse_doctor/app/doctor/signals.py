"""Where the doctor looks.

Two sources, on purpose:
- Jobs API: real-time (a failure shows up seconds after it happens). Used for live incidents.
- System tables: history + cost, but they can lag (minutes to hours). Used for trends,
  "is this job flaky or newly broken?", and $.

TODO(you): verify column names against your workspace (Catalog -> system -> lakeflow/billing/query).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

FAILED_STATES = {"FAILED", "TIMEDOUT", "INTERNAL_ERROR", "CANCELED"}


def _w():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient()


# ---------------- Jobs API (live) ----------------

def recent_failures(hours: int = 24, limit: int = 50) -> list[dict]:
    w = _w()
    since_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
    out = []
    for run in w.jobs.list_runs(completed_only=True, start_time_from=since_ms, limit=25):
        state = run.state.result_state.value if run.state and run.state.result_state else None
        if state in FAILED_STATES:
            out.append({
                "job_id": run.job_id, "run_id": run.run_id, "run_name": run.run_name,
                "result_state": state, "state_message": (run.state.state_message or "")[:300],
                "start_time": datetime.fromtimestamp(run.start_time / 1000, timezone.utc).isoformat(),
                "run_page_url": run.run_page_url,
            })
        if len(out) >= limit:
            break
    return out


def run_error(run_id: int) -> dict:
    """Error message + trace + notebook path for each failed task of a run."""
    w = _w()
    run = w.jobs.get_run(run_id)
    tasks = []
    for t in run.tasks or []:
        st = t.state.result_state.value if t.state and t.state.result_state else None
        if st not in FAILED_STATES:
            continue
        out = w.jobs.get_run_output(t.run_id)
        tasks.append({
            "task_key": t.task_key,
            "notebook_path": t.notebook_task.notebook_path if t.notebook_task else None,
            "error": (out.error or "")[:1000],
            "error_trace": (out.error_trace or "")[-3000:],  # the end of the trace is the useful part
        })
    return {"run_id": run_id, "job_id": run.job_id, "run_name": run.run_name, "failed_tasks": tasks}


def source_code(notebook_path: str, max_chars: int = 6000) -> dict:
    from databricks.sdk.service.workspace import ExportFormat

    w = _w()
    exp = w.workspace.export(notebook_path, format=ExportFormat.SOURCE)
    code = base64.b64decode(exp.content).decode("utf-8", errors="replace")
    lines = code.splitlines()
    numbered = "\n".join(f"{i + 1:4d}  {l}" for i, l in enumerate(lines))
    return {"path": notebook_path, "source": numbered[:max_chars], "truncated": len(numbered) > max_chars}


def table_info(full_name: str) -> dict:
    """Does the table exist? Columns? If not, which similarly named tables exist (typo hunting)."""
    w = _w()
    try:
        t = w.tables.get(full_name)
        return {"exists": True, "columns": [(c.name, c.type_text) for c in (t.columns or [])]}
    except Exception as e:
        parts = full_name.split(".")
        siblings = []
        if len(parts) == 3:
            try:
                siblings = [t.name for t in w.tables.list(catalog_name=parts[0], schema_name=parts[1])]
            except Exception:
                pass
        return {"exists": False, "error": str(e)[:200], "tables_in_schema": siblings[:50]}


def rerun(run_id: int) -> dict:
    """Repair the run (re-runs only the failed tasks). Called only after human approval."""
    w = _w()
    r = w.jobs.repair_run(run_id=run_id, rerun_all_failed_tasks=True)
    return {"repair_id": r.repair_id}


# ---------------- System tables (history + cost) ----------------
# These take a `db` with .query(sql, params) -> list[dict] (SparkSql in notebooks, WarehouseSql in the app).

def job_history(db, job_id: int, n: int = 30) -> list[dict]:
    return db.query(
        f"""SELECT run_id, period_start_time, result_state, termination_code
            FROM system.lakeflow.job_run_timeline
            WHERE job_id = :job_id AND result_state IS NOT NULL
            ORDER BY period_start_time DESC LIMIT {int(n)}""",
        {"job_id": str(job_id)},
    )


def top_cost_drivers(db, days: int = 7, n: int = 10) -> list[dict]:
    return db.query(
        f"""SELECT u.billing_origin_product AS product,
                   coalesce(u.usage_metadata.job_id, u.usage_metadata.warehouse_id,
                            u.usage_metadata.dlt_pipeline_id, u.usage_metadata.notebook_id) AS resource_id,
                   round(sum(u.usage_quantity), 3) AS dbus,
                   round(sum(u.usage_quantity * p.pricing.default), 2) AS list_cost_usd
            FROM system.billing.usage u
            LEFT JOIN system.billing.list_prices p
              ON u.sku_name = p.sku_name AND u.usage_start_time >= p.price_start_time
             AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
            WHERE u.usage_date >= current_date() - INTERVAL {int(days)} DAYS
            GROUP BY ALL ORDER BY dbus DESC LIMIT {int(n)}"""
    )


def slow_queries(db, days: int = 1, n: int = 10) -> list[dict]:
    return db.query(
        f"""SELECT statement_id, executed_by, total_duration_ms, read_bytes, produced_rows,
                   left(statement_text, 400) AS statement_text
            FROM system.query.history
            WHERE start_time >= current_timestamp() - INTERVAL {int(days)} DAYS
              AND execution_status = 'FINISHED'
            ORDER BY total_duration_ms DESC LIMIT {int(n)}"""
    )


def apply_patch(notebook_path: str, before: str, after: str) -> dict:
    """Replace `before` with `after` in a notebook, only if `before` occurs exactly once.
    Saves the original next to it as <name>_backup_<timestamp> first. Human-approved only."""
    import io

    from databricks.sdk.service.workspace import ExportFormat, ImportFormat, Language

    w = _w()
    exp = w.workspace.export(notebook_path, format=ExportFormat.SOURCE)
    code = base64.b64decode(exp.content).decode("utf-8")
    n = code.count(before) if before else 0
    if n != 1:
        return {"applied": False, "reason": f"'before' text found {n} times (need exactly 1)"}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = f"{notebook_path}_backup_{stamp}"
    w.workspace.upload(backup, io.BytesIO(code.encode()), format=ImportFormat.SOURCE,
                       language=Language.PYTHON, overwrite=True)
    w.workspace.upload(notebook_path, io.BytesIO(code.replace(before, after).encode()),
                       format=ImportFormat.SOURCE, language=Language.PYTHON, overwrite=True)
    return {"applied": True, "backup": backup}
