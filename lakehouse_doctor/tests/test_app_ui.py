"""Headless UI tests in demo mode (DOCTOR_FAKE=1): every page renders without exceptions,
and the core flow (diagnose -> verified fix -> apply) works end to end."""
import os
import sys

import pytest

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)
os.environ["DOCTOR_FAKE"] = "1"

st_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = st_testing.AppTest

PAGES = ["overview", "incidents", "explorer", "ask_genie", "costs", "log", "lab"]


def run_page(name):
    at = AppTest.from_file(os.path.join(APP, "views", f"{name}.py"), default_timeout=30)
    at.run()
    return at


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page):
    at = run_page(page)
    assert not at.exception, [e.value for e in at.exception]


def test_main_app_renders_overview():
    at = AppTest.from_file(os.path.join(APP, "app.py"), default_timeout=30)
    at.run()
    assert not at.exception
    assert any("Lakehouse Doctor" in m.value for m in at.markdown)


def test_diagnose_then_apply_flow():
    at = run_page("incidents")
    # the newest open incident (upstream drift) is selected and undiagnosed -> Diagnose button
    diag = [b for b in at.button if b.label == "🩺 Diagnose"]
    assert diag, "expected a Diagnose button for an undiagnosed incident"
    diag[0].click().run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Not this job's fault" in text and "Verified fix" in text
    apply = [b for b in at.button if b.label == "✅ Apply fix & rerun"]
    assert apply and not apply[0].disabled
    apply[0].click().run()
    assert not at.exception


def test_lineage_dot_highlights_culprit():
    from ui.components import lineage_dot
    from ui.fake import EDGES
    dot = lineage_dot(EDGES, culprit_table="workspace.doctor.orders_clean",
                      culprit_job="doctor-chaos-chain_ingest_orders", blast=["workspace.doctor.customer_ltv"])
    assert '"workspace.doctor.orders_clean" [label="orders_clean"' in dot and "#F87171" in dot and "#FBBF24" in dot
