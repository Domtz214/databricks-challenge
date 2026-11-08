"""Lakehouse Doctor: Databricks App entry point (Streamlit multipage)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st  # noqa: E402

from ui.backend import get_backend, current_user  # noqa: E402
from ui.components import chip, inject_css  # noqa: E402

st.set_page_config(page_title="Lakehouse Doctor", page_icon="🩺", layout="wide")
inject_css()

pages = {
    "Operate": [
        st.Page("views/overview.py", title="Overview", icon="🩺", default=True),
        st.Page("views/incidents.py", title="Incidents", icon="🚨"),
        st.Page("views/explorer.py", title="Lineage explorer", icon="🕸️"),
    ],
    "Ask & analyze": [
        st.Page("views/ask_genie.py", title="Ask Genie", icon="🧞"),
        st.Page("views/costs.py", title="Costs", icon="💸"),
        st.Page("views/log.py", title="Incident log", icon="📜"),
    ],
    "Demo": [st.Page("views/lab.py", title="Chaos lab", icon="🧪")],
}
nav = st.navigation(pages)

with st.sidebar:
    be = get_backend()
    st.markdown("### 🩺 Lakehouse Doctor")
    st.markdown(chip("demo data", "violet") if be.mode == "demo" else chip("live workspace", "green"),
                unsafe_allow_html=True)
    st.caption(f"Signed in as {current_user()}")
    if st.button("↻ Refresh data", use_container_width=True):
        be.clear_cache()
        st.rerun()

nav.run()
