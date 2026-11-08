import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from doctor import agent  # noqa: E402


def test_parse_json_extracts_object():
    assert agent.parse_json('Sure! {"summary": "x", "confidence": 0.9} done')["summary"] == "x"
    assert "error" in agent.parse_json("no json here")


def test_tool_schemas_match_registry():
    names = {t["function"]["name"] for t in agent.TOOLS}
    d = agent.Doctor(db=None, client=object())
    assert names == set(d.tools)


class FakeClient:
    """Scripted model: first asks for run_error, then answers."""

    def __init__(self):
        self.calls = 0
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            tc = NS(id="1", function=NS(name="run_error", arguments=json.dumps({"run_id": 7})),
                    model_dump=lambda: {"id": "1", "type": "function",
                                        "function": {"name": "run_error", "arguments": "{}"}})
            msg = NS(content="", tool_calls=[tc])
        else:
            msg = NS(content=json.dumps({"summary": "typo in table name", "category": "missing_table"}),
                     tool_calls=None)
        return NS(choices=[NS(message=msg)])


def test_diagnose_loop_uses_tools_then_answers(monkeypatch):
    monkeypatch.setattr(agent.signals, "run_error", lambda run_id: {"run_id": run_id, "failed_tasks": []})
    d = agent.Doctor(db=None, client=FakeClient())
    dx = d.diagnose(7)
    assert dx["category"] == "missing_table" and dx["run_id"] == 7
    assert [t["tool"] for t in d.trace] == ["run_error"]
