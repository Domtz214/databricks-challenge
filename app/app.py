"""Triage console (Databricks App).

Before deploying: run scripts/sync_app.sh so app/triage/ contains the shared package.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Triage", page_icon="🛟", layout="wide")

pages = [
    st.Page("views/console.py", title="Dispatch console", icon="🗺️", default=True),
    st.Page("views/forecast.py", title="Forecast", icon="📈"),
    st.Page("views/report.py", title="Report an emergency", icon="📱"),
    st.Page("views/proof.py", title="Harvey back-test", icon="🧪"),
]
st.navigation(pages).run()
