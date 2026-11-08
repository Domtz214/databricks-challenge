"""Dispatcher agent: a small tool-calling loop against a Databricks
Foundation Model endpoint (OpenAI-compatible), traced with MLflow.

Usage (notebook):
    from triage.db import SparkSql
    from triage.agent import TriageAgent
    rec = TriageAgent(SparkSql(spark)).recommend("<incident_id>")
"""

from __future__ import annotations

import json
import re
from typing import Any

from .. import config
from ..db import Sql
from . import prompts
from .tools import TOOL_SCHEMAS, make_tool_registry, nearest_facilities, search_guidance, to_json

try:
    import mlflow

    _trace = mlflow.trace
except Exception:  # mlflow not installed (e.g. local tests)
    def _trace(fn=None, **_):
        return fn if fn else (lambda f: f)


def _client():
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient().serving_endpoints.get_open_ai_client()


def _parse_json(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {"error": "model did not return JSON", "raw": text}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"error": "invalid JSON", "raw": text}


class TriageAgent:
    def __init__(self, db: Sql, endpoint: str = config.LLM_ENDPOINT, max_steps: int = 8, client=None):
        self.db = db
        self.endpoint = endpoint
        self.max_steps = max_steps
        self.client = client or _client()
        self.tools = make_tool_registry(db)

    @_trace(name="triage_recommend")
    def recommend(self, incident_id: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompts.DISPATCHER_SYSTEM},
            {"role": "user", "content": f"Incident id: {incident_id}"},
        ]
        for _ in range(self.max_steps):
            resp = self.client.chat.completions.create(
                model=self.endpoint, messages=messages, tools=TOOL_SCHEMAS, temperature=0.1,
            )
            msg = resp.choices[0].message
            if not msg.tool_calls:
                out = _parse_json(msg.content)
                out["incident_id"] = incident_id
                return out
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in msg.tool_calls],
            })
            for tc in msg.tool_calls:
                result = self._call_tool(tc.function.name, tc.function.arguments)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": to_json(result)})
        return {"incident_id": incident_id, "error": "max_steps reached"}

    @_trace(span_type="TOOL")
    def _call_tool(self, name: str, arguments: str) -> Any:
        try:
            args = json.loads(arguments or "{}")
            return self.tools[name](**args)
        except Exception as e:  # surface tool errors to the model instead of crashing
            return {"error": f"{type(e).__name__}: {e}"}

    @_trace(name="reporter_reply")
    def reply_to_reporter(self, text: str, lang: str, lat: float | None, lon: float | None,
                          category: str | None = None, tier: str | None = None) -> str:
        """Fast single-call reply shown on the reporter's phone right after submit."""
        shelters = nearest_facilities(self.db, lat, lon, "shelter", 2) if lat is not None else []
        guidance = search_guidance(self.db, f"{category or 'flood'} safety instructions for residents", 2)
        resp = self.client.chat.completions.create(
            model=self.endpoint,
            temperature=0.2,
            messages=[
                {"role": "system", "content": prompts.REPORTER_REPLY_SYSTEM.format(lang=lang)},
                {"role": "user", "content": to_json({
                    "report": text, "category": category, "priority": tier,
                    "nearest_shelters": shelters, "guidance": guidance,
                })},
            ],
        )
        return resp.choices[0].message.content.strip()

    @_trace(name="assess_photo")
    def assess_photo(self, image_b64: str, mime: str = "image/jpeg") -> dict[str, Any]:
        """Damage severity from a photo via a multimodal endpoint.
        TODO(you): confirm config.VISION_ENDPOINT accepts image input (try it in Playground)."""
        resp = self.client.chat.completions.create(
            model=config.VISION_ENDPOINT,
            messages=[
                {"role": "system", "content": prompts.PHOTO_SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": "Assess this photo."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                ]},
            ],
        )
        return _parse_json(resp.choices[0].message.content)
