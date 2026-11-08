"""The doctor: a tool-calling loop over a Databricks Foundation Model endpoint."""

from __future__ import annotations

import json
import re
from typing import Any

from . import config, signals

SYSTEM = """You are Lakehouse Doctor, a senior Databricks reliability engineer.
Diagnose the failed job run you are given. Investigate before concluding:
1. run_error for the error and stack trace,
2. source_code for the failing notebook (line numbers included),
3. table_info for any table named in the error (check for typos / missing columns),
4. job_history to tell a NEW failure from a FLAKY one.
Then answer with ONLY this JSON:
{
  "summary": "one sentence a manager understands",
  "root_cause": "precise technical cause",
  "category": "missing_table|schema_mismatch|data_quality|code_bug|permissions|resource|flaky|other",
  "evidence": ["each item cites a tool result, e.g. 'line 12 reads workspace.doctor.ordres'"],
  "fix": {"type": "code_patch|config|data|rerun", "description": str,
          "before": "exact line(s) to replace or null", "after": "replacement or null"},
  "prevention": "one guardrail, e.g. a pipeline expectation or schema check",
  "confidence": 0.0-1.0,
  "safe_to_rerun_after_fix": true|false
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
]


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
        }
        self.trace: list[dict] = []   # tool calls, shown in the app ("how the doctor reasoned")

    def diagnose(self, run_id: int) -> dict[str, Any]:
        self.trace = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Diagnose failed run_id {run_id}."},
        ]
        for _ in range(self.max_steps):
            msg = self.client.chat.completions.create(
                model=self.endpoint, messages=messages, tools=TOOLS, temperature=0.1
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

    def _call(self, name: str, arguments: str) -> Any:
        try:
            return self.tools[name](**json.loads(arguments or "{}"))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
