import pandas as pd
import streamlit as st

from ui.backend import get_backend
from ui.components import hero

be = get_backend()
hero("Ask Genie", "Plain-English questions over incidents, lineage, job runs and cost. The doctor uses this same space.")

if not be.genie_enabled():
    st.info("Connect a Genie space: run `lakehouse_doctor/notebooks/03_genie_space_setup`, create the space, and set "
            "`DOCTOR_GENIE_SPACE_ID` in `app/app.yaml`.")
    st.stop()

SUGGESTIONS = ["Which jobs failed most this week?", "What is downstream of workspace.doctor.orders_clean?",
               "How many incidents were fixed vs dismissed?", "Average o_totalprice per day this week"]
chat = st.session_state.setdefault("genie_chat", [])

cols = st.columns(len(SUGGESTIONS))
picked = None
for c, s in zip(cols, SUGGESTIONS):
    if c.button(s, use_container_width=True):
        picked = s

for m in chat:
    with st.chat_message(m["role"], avatar="🧞" if m["role"] == "assistant" else None):
        if m.get("text"):
            st.markdown(m["text"])
        if m.get("rows"):
            st.dataframe(pd.DataFrame(m["rows"], columns=m["columns"]), hide_index=True, use_container_width=True)
        if m.get("sql"):
            with st.expander("SQL Genie wrote"):
                st.code(m["sql"], language="sql")

q = st.chat_input("Ask about failures, lineage, cost…") or picked
if q:
    chat.append({"role": "user", "text": q})
    with st.spinner("Genie is thinking…"):
        try:
            ans = be.genie_ask(q, st.session_state.get("genie_conv"))
            st.session_state.genie_conv = ans["conversation_id"]
            chat.append({"role": "assistant", **ans})
        except Exception as e:
            chat.append({"role": "assistant", "text": f"Genie error: {e}"})
    st.rerun()
