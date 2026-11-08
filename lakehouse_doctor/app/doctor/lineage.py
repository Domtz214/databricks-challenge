"""Lineage + change forensics without relying on system.access.table_lineage
(which can be empty or lag). Two sources:

1. Code lineage: parse every job's notebook source for tables it reads and writes.
2. Table history: Delta `DESCRIBE HISTORY` says which job/user wrote each version,
   and comparing versions shows schema + value changes ("what changed upstream?").

If system.access.table_lineage has rows, `system_lineage` uses it too.
"""

from __future__ import annotations

import base64
import re
from collections import defaultdict

NAME = r"`?([A-Za-z_][\w]*)`?\.`?([A-Za-z_][\w]*)`?\.`?([A-Za-z_][\w]*)`?"
READ_PATTERNS = [
    rf"""(?:read|readStream)\s*\.\s*table\(\s*["']{NAME}["']""",
    rf"""spark\.table\(\s*["']{NAME}["']""",
    rf"""\b(?:FROM|JOIN)\s+{NAME}""",
]
WRITE_PATTERNS = [
    rf"""saveAsTable\(\s*["']{NAME}["']""",
    rf"""\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{NAME}""",
    rf"""\bINSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?{NAME}""",
    rf"""\bMERGE\s+INTO\s+{NAME}""",
]


def extract_tables(code: str) -> dict[str, set[str]]:
    """{'reads': {...}, 'writes': {...}} of 3-part table names found in notebook code."""
    def find(patterns):
        out = set()
        for p in patterns:
            for m in re.finditer(p, code, re.I):
                out.add(".".join(g.lower() for g in m.groups()[-3:]))
        return out

    writes = find(WRITE_PATTERNS)
    reads = find(READ_PATTERNS) - writes  # CREATE ... AS SELECT FROM x: x is a read, target is a write
    return {"reads": reads, "writes": writes}


def build_code_lineage(max_jobs: int = 200) -> list[dict]:
    """Edges {source, target, job_id, job_name, notebook_path} from all jobs' notebook code."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.workspace import ExportFormat

    w = WorkspaceClient()
    edges, seen_nb = [], {}
    for i, job in enumerate(w.jobs.list(expand_tasks=True)):
        if i >= max_jobs:
            break
        for t in (job.settings.tasks or []) if job.settings else []:
            path = t.notebook_task.notebook_path if t.notebook_task else None
            if not path:
                continue
            if path not in seen_nb:
                try:
                    code = base64.b64decode(w.workspace.export(path, format=ExportFormat.SOURCE).content).decode()
                    seen_nb[path] = extract_tables(code)
                except Exception:
                    seen_nb[path] = {"reads": set(), "writes": set()}
            io = seen_nb[path]
            for tgt in io["writes"]:
                for src in io["reads"] or {None}:
                    edges.append({"source": src, "target": tgt, "job_id": job.job_id,
                                  "job_name": job.settings.name, "notebook_path": path})
            if not io["writes"]:  # pure reader (e.g. a dashboard feed): still record what it depends on
                for src in io["reads"]:
                    edges.append({"source": src, "target": None, "job_id": job.job_id,
                                  "job_name": job.settings.name, "notebook_path": path})
    return edges


def system_lineage(db, table: str) -> list[dict]:
    try:
        return db.query(
            """SELECT DISTINCT source_table_full_name AS source, target_table_full_name AS target,
                      entity_type, entity_id
               FROM system.access.table_lineage
               WHERE (source_table_full_name = :t OR target_table_full_name = :t)
                 AND event_time >= current_timestamp() - INTERVAL 30 DAYS LIMIT 200""",
            {"t": table},
        )
    except Exception:
        return []


def neighborhood(edges: list[dict], table: str) -> dict:
    """Writers of `table`, its upstream tables, and everything downstream (blast radius)."""
    table = table.lower()
    writers = [e for e in edges if e["target"] == table]
    upstream = sorted({e["source"] for e in writers if e["source"]})
    down_children = defaultdict(list)
    for e in edges:
        if e["source"]:
            down_children[e["source"]].append(e)
    blast, stack, seen = [], [table], {table}
    while stack:
        cur = stack.pop()
        for e in down_children.get(cur, []):
            blast.append({"via_job": e["job_name"], "reads": cur, "writes": e["target"]})
            if e["target"] and e["target"] not in seen:
                seen.add(e["target"]); stack.append(e["target"])
    return {
        "table": table,
        "written_by_jobs": sorted({(e["job_id"], e["job_name"]) for e in writers}),
        "upstream_tables": upstream,
        "downstream_blast_radius": blast,
    }


# ------------------------- table history forensics -------------------------

def _as_dict(x):
    if x is None:
        return None
    if hasattr(x, "asDict"):
        return x.asDict(recursive=True)
    return dict(x) if not isinstance(x, dict) else x


def table_history(db, table: str, n: int = 10) -> list[dict]:
    rows = db.query(f"DESCRIBE HISTORY {table} LIMIT {int(n)}")
    out = []
    for r in rows:
        job = _as_dict(r.get("job")) or {}
        out.append({
            "version": r.get("version"), "timestamp": str(r.get("timestamp")),
            "operation": r.get("operation"), "user": r.get("userName"),
            "job_id": job.get("jobId"), "job_name": job.get("jobName"), "job_run_id": job.get("runId") or job.get("jobRunId"),
            "parameters": {k: str(v)[:120] for k, v in (_as_dict(r.get("operationParameters")) or {}).items()},
        })
    return out


def schema_at_version(db, table: str, version: int) -> dict[str, str]:
    row = db.query(f"SELECT * FROM {table} VERSION AS OF {int(version)} LIMIT 1")
    if not row:
        return {}
    cols = list(row[0].keys())
    types = db.query("SELECT " + ", ".join(f"typeof(`{c}`) AS `{c}`" for c in cols)
                     + f" FROM {table} VERSION AS OF {int(version)} LIMIT 1")
    return dict(types[0]) if types else {}


NUMERIC = ("int", "bigint", "smallint", "tinyint", "double", "float", "decimal")


def diff_schemas(before: dict[str, str], after: dict[str, str]) -> dict:
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "type_changed": {c: f"{before[c]} -> {after[c]}" for c in set(before) & set(after) if before[c] != after[c]},
    }


def schema_diff(db, table: str, from_version: int | None = None, to_version: int | None = None) -> dict:
    """What changed between two versions (default: the last two): schema + mean of numeric columns."""
    if from_version is None or to_version is None:
        hist = table_history(db, table, 2)
        if len(hist) < 2:
            return {"error": "fewer than 2 versions"}
        to_version, from_version = hist[0]["version"], hist[1]["version"]
    b, a = schema_at_version(db, table, from_version), schema_at_version(db, table, to_version)
    out = {"table": table, "from_version": from_version, "to_version": to_version, **diff_schemas(b, a)}
    common_numeric = [c for c in set(a) & set(b) if a[c].startswith(NUMERIC) and b[c].startswith(NUMERIC)]
    if common_numeric:
        def means(v):
            r = db.query("SELECT " + ", ".join(f"avg(`{c}`) AS `{c}`" for c in common_numeric)
                         + f" FROM {table} VERSION AS OF {int(v)}")
            return r[0] if r else {}
        mb, ma = means(from_version), means(to_version)
        out["numeric_mean_ratio"] = {c: round(float(ma[c]) / float(mb[c]), 3)
                                     for c in common_numeric if mb.get(c) not in (None, 0) and ma.get(c) is not None}
    return out
