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
4. job_history: is this failure new or flaky?
5. ask_genie (if available): confirm data facts in plain English, e.g. "average o_totalprice by day this week".
If an upstream change caused the failure, the fix belongs UPSTREAM; a downstream cast is only a band-aid.

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
    def __init__(self, db, client=None, endpoint: str = config.LLM_ENDPOINT, max_steps: int = 8):
        self.db = db
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

    def diagnose(self, run_id: int) -> dict[str, Any]:
        self.trace = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Diagnose failed run_id {run_id}."},
        ]
        for _ in range(self.max_steps):
            msg = self.client.chat.completions.create(
                model=self.endpoint, messages=messages, tools=self.tool_schemas, temperature=0.1
            ).choices[0].message
            if not msg.tool_calls:
                out = parse_json(msg.content)
                out["run_id"] = run_id
                return out
            messages.append({"role": "assistant", "content": msg.content or "",
                             "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                result = self._call(tc.function.name, tc.function.arguments)
                self.trace.append({"tool": tc.function.name, "args": tc.function.arguments,
                                   "result_preview": json.dumps(result, default=str)[:400]})
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": json.dumps(result, default=str)[:8000]})
        return {"run_id": run_id, "error": "max_steps reached"}

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
