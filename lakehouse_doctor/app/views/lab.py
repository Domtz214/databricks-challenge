import streamlit as st

from ui.backend import SCENARIOS, get_backend
from ui.components import chip, hero

be = get_backend()
hero("Chaos lab", "Break the workspace on purpose, then watch the doctor work. Each scenario runs real jobs.")

jobs = be.chaos_jobs()
if not jobs:
    st.warning("No chaos jobs found. Run `lakehouse_doctor/notebooks/00_setup_and_break_things` and "
               "`02_upstream_drift_scenario` once to create them.")

cols = st.columns(3)
for idx, (key, sc) in enumerate(SCENARIOS.items()):
    with cols[idx % 3]:
        with st.container(border=True):
            st.markdown(f"#### {sc['icon']} {sc['title']}")
            st.markdown(sc["story"])
            st.markdown(chip("expect: " + sc["expect"], "teal"), unsafe_allow_html=True)
            if st.button("Run scenario", key=f"run_{key}", type="primary" if key == "upstream_drift" else "secondary",
                         use_container_width=True):
                with st.status(f"{sc['title']}…", expanded=True) as s:
                    be.run_scenario(key, on_step=s.write)
                    s.update(label=f"{sc['title']}: started", state="complete")
                st.toast("Failures appear in 🚨 Incidents within a minute or two.", icon="🧪")

st.caption("Applied fixes edit the chaos notebooks (a backup is kept). To restore the original broken versions, "
           "run `notebooks/99_update_from_github`.")
