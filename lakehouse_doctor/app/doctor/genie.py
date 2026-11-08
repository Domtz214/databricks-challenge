"""Genie as a collaborator, not a competitor.

The doctor asks a Genie space data questions in plain English during an investigation
("did average o_totalprice change after version 14?"). Genie owns trusted NL->SQL over a
curated space; the doctor owns cross-workspace orchestration. The same space powers the
app's "Ask Genie" tab for humans.

Needs DOCTOR_GENIE_SPACE_ID (create the space in SQL -> Genie, see README).
"""

from __future__ import annotations

import os

def space_id() -> str:
    return os.getenv("DOCTOR_GENIE_SPACE_ID", "")  # read lazily so notebooks can set it after import


def enabled() -> bool:
    return bool(space_id())


def ask(question: str, conversation_id: str | None = None, max_rows: int = 20) -> dict:
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient()
    SPACE_ID = space_id()
    if conversation_id:
        msg = w.genie.create_message_and_wait(SPACE_ID, conversation_id, question)
    else:
        msg = w.genie.start_conversation_and_wait(SPACE_ID, question)
    out = {"conversation_id": msg.conversation_id, "text": None, "sql": None, "columns": [], "rows": []}
    for att in msg.attachments or []:
        if att.text and att.text.content:
            out["text"] = att.text.content
        if att.query:
            out["sql"] = att.query.query
            out["text"] = out["text"] or att.query.description
            # SDK method name differs across versions.
            getter = (getattr(w.genie, "get_message_attachment_query_result", None)
                      or getattr(w.genie, "get_message_query_result_by_attachment"))
            res = getter(SPACE_ID, msg.conversation_id, msg.id, att.attachment_id)
            sr = res.statement_response
            if sr and sr.manifest and sr.result:
                out["columns"] = [c.name for c in sr.manifest.schema.columns]
                out["rows"] = (sr.result.data_array or [])[:max_rows]
    return out
