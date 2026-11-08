"""The doctor: a tool-calling loop over a Databricks Foundation Model endpoint."""

from __future__ import annotations

import json
import re
from typing import Any

from . import config, genie, lineage, signals

SYSTEM = """You are Lakehouse Doctor, a senior Databricks reliability engineer.
Built-in assistants fix the line of code in front of them. Your job is to find the INCIDENT behind it:
the true root cause (often upstream), what else is affected, and the fix in the right place.

Investigate before concluding:
1. run_error: error + stack trace of the failed run.
2. source_code: the failing notebook (line numbers included).
3. For every table the failing code reads: table_info, then lineage (who writes it, what is downstream),
   then table_history + schema_diff to see whether an UPSTREAM job recently changed its schema or values.
4. If an upstream job changed the table: run_details on the job_run_id from table_history (which parameters
   did it run with?) and source_code on THAT job's notebook. Find the exact line(s) that introduced the change.
5. job_history: is this failure new or flaky?
6. ask_genie (if available): confirm data facts in plain English, e.g. "average o_totalprice by day this week".
If an upstream change caused the failure, the fix belongs UPSTREAM; a downstream cast is only a band-aid.

Rules for the answer:
- fix.notebook_path is the notebook that must change (the upstream one if fix_location = upstream_job).
- fix.before must be copied VERBATIM from source_code output of fix.notebook_path, WITHOUT the line-number
  prefix, and must occur exactly once. fix.after replaces it. Your fix is verified automatically.
- genie_code_prompt is a plain-English instruction for a coding assistant (never SQL).
- Each evidence item quotes concrete facts: error class, table version numbers, line numbers, parameter values.

Answer with ONLY this JSON:
{
  "summary": "one sentence a manager understands",
  "root_cause": "precise technical cause",
  "category": "missing_table|schema_drift|value_drift|data_quality|code_bug|permissions|resource|flaky|other",
  "fix_location": "this_job|upstream_job",
  "upstream_culprit": {"job_name": str, "job_id": int, "table": str, "version": int, "timestamp": str,
                       "change": "e.g. o_totalprice DECIMAL -> STRING"} | null,
  "blast_radius": ["downstream tables/jobs also affected"],
  "evidence": ["each item cites a tool result"],
  "fix": {"type": "code_patch|config|data|rerun", "notebook_path": str, "description": str,
          "before": "exact line(s) to replace, copied from source_code, or null", "after": "replacement or null"},
  "genie_code_prompt": "a precise instruction a developer can paste into Genie Code / Databricks Assistant
                        in the notebook to implement the fix",
  "prevention": "one guardrail (expectation, schema check, contract)",
  "confidence": 0.0-1.0
}
Never invent evidence. If unsure, say so and lower confidence."""

TOOLS = [
    {"type": "function", "function": {"name": "run_error", "description": "Errors and stack traces of the failed tasks in a run.",
     "parameters": {"type": "object", "properties": {"run_id": {"type": "integer"}}, "required": ["run_id"]}}},
    {"type": "function", "function": {"name": "source_code", "description": "Source of a notebook, with line numbers.",
     "parameters": {"type": "object", "properties": {"notebook_path": {"type": "string"}}, "required": ["notebook_path"]}}},
    {"type": "function", "function": {"name": "table_info", "description": "Whether a table exists, its columns, or similarly named tables.",
     "parameters": {"type": "object", "properties": {"full_name": {"type": "string"}}, "required": ["full_name"]}}},
    {"type": "function", "function": {"name": "job_history", "description": "Recent run outcomes of a job (system tables).",
     "parameters": {"type": "object", "properties": {"job_id": {"type": "integer"}}, "required": ["job_id"]}}},
    {"type": "function", "function": {"name": "run_details", "description": "A run's job, notebook paths, parameters and state (e.g. the upstream run that wrote a table version).",
     "parameters": {"type": "object", "properties": {"run_id": {"type": "integer"}}, "required": ["run_id"]}}},
    {"type": "function", "function": {"name": "lineage", "description": "Which jobs write a table, its upstream tables, and its downstream blast radius.",
     "parameters": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}}},
    {"type": "function", "function": {"name": "table_history", "description": "Recent Delta versions of a table: when, which job/user, which operation.",
     "parameters": {"type": "object", "properties": {"table": {"type": "string"}}, "required": ["table"]}}},
    {"type": "function", "function": {"name": "schema_diff", "description": "Schema changes and numeric mean ratios between two versions (default: last two).",
     "parameters": {"type": "object", "properties": {"table": {"type": "string"}, "from_version": {"type": "integer"},
                                                     "to_version": {"type": "integer"}}, "required": ["table"]}}},
]
GENIE_TOOL = {"type": "function", "function": {"name": "ask_genie",
              "description": "Ask the workspace's Genie space a data question in plain English; returns its SQL and result.",
              "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}}}


def parse_json(text: str | None) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {"error": "no JSON in model output", "raw": text}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"error": "invalid JSON", "raw": text}


class Doctor:
    def __init__(self, db, client=None, endpoint: str = config.LLM_ENDPOINT, max_steps: int = 10,
                 max_repairs: int = 2):
        self.db = db
        self.max_repairs = max_repairs
        self.endpoint = endpoint
        self.max_steps = max_steps
        if client is None:
            from databricks.sdk import WorkspaceClient

            client = WorkspaceClient().serving_endpoints.get_open_ai_client()
        self.client = client
        self.tools = {
            "run_error": lambda **a: signals.run_error(**a),
            "source_code": lambda **a: signals.source_code(**a),
            "table_info": lambda **a: signals.table_info(**a),
            "job_history": lambda **a: signals.job_history(self.db, **a),
            "run_details": lambda **a: signals.run_details(**a),
            "lineage": lambda **a: self._lineage(**a),
            "table_history": lambda **a: lineage.table_history(self.db, **a),
            "schema_diff": lambda **a: lineage.schema_diff(self.db, **a),
        }
        self.tool_schemas = list(TOOLS)
        if genie.enabled():
            self.tools["ask_genie"] = lambda **a: genie.ask(**a)
            self.tool_schemas.append(GENIE_TOOL)
        self._edges = None
        self.trace: list[dict] = []   # tool calls, shown in the app ("how the doctor reasoned")

    def diagnose(self, run_id: int, on_event=None) -> dict[str, Any]:
        """on_event(step_dict) is called after every tool call / verification (for live UIs)."""
        self.trace = []
        emit = on_event or (lambda e: None)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Diagnose failed run_id {run_id}."},
        ]
        repairs = 0
        for _ in range(self.max_steps + self.max_repairs):
            msg = self.client.chat.completions.create(
                model=self.endpoint, messages=messages, tools=self.tool_schemas, temperature=0.1
            ).choices[0].message
            if not msg.tool_calls:
                out = parse_json(msg.content)
                problem = self.check_answer(out)
                if problem and repairs < self.max_repairs:
                    repairs += 1
                    self.trace.append({"tool": "verify_fix", "args": "", "result_preview": problem})
                    emit(self.trace[-1])
                    messages.append({"role": "assistant", "content": msg.content or ""})
                    messages.append({"role": "user", "content": "Verification failed: " + problem + " Fix it and answer again with the full JSON."})
                    continue
                fix = out.get("fix") or {}
                out["fix_verified"] = problem is None and bool(fix.get("before"))
                out["verification"] = problem or ("fix.before found exactly once in " + str(fix.get("notebook_path"))
                                                  if fix.get("before") else "no code patch proposed")
                out["run_id"] = run_id
                return out
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                result = self._call(tc.function.name, tc.function.arguments)
                self.trace.append({"tool": tc.function.name, "args": tc.function.arguments,
                                   "result_preview": json.dumps(result, default=str)[:400]})
                emit(self.trace[-1])
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, default=str)[:8000]})
        return {"run_id": run_id, "error": "max_steps reached"}

    def check_answer(self, out: dict) -> str | None:
        """Deterministic checks the model can't talk its way past. Returns a problem or None."""
        if "error" in out:
            return "Your answer was not valid JSON."
        fix = out.get("fix") or {}
        before, path = fix.get("before"), fix.get("notebook_path")
        if before:
            if not path:
                return "fix.notebook_path is missing."
            try:
                code = signals.raw_source(path)
            except Exception as e:
                return f"Could not read {path}: {e}."
            n = code.count(before)
            if n != 1:
                return (f"fix.before was found {n} times in {path} (must be exactly 1). Call "
                        f"source_code(notebook_path='{path}') and copy the exact line(s) without line numbers.")
            if fix.get("after") is None or fix.get("after") == before:
                return "fix.after must differ from fix.before."
        if re.match(r"\s*(alter|select|update|create|insert|drop|merge)\b", out.get("genie_code_prompt") or "", re.I):
            return "genie_code_prompt must be a plain-English instruction, not SQL."
        return None

    def _lineage(self, table: str) -> dict:
        if self._edges is None:  # built once per Doctor (scans job notebooks)
            self._edges = lineage.build_code_lineage()
        out = lineage.neighborhood(self._edges, table)
        sys_rows = lineage.system_lineage(self.db, table) if self.db is not None else []
        if sys_rows:
            out["system_table_lineage"] = sys_rows[:50]
        return out

    def _call(self, name: str, arguments: str) -> Any:
        try:
            return self.tools[name](**json.loads(arguments or "{}"))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
