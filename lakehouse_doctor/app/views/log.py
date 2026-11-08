import pandas as pd
import streamlit as st

from ui.backend import get_backend
from ui.components import hero
from ui.components import short_job

be = get_backend()
hero("Incident log", "Every diagnosis and every human decision. Stored in Delta, queryable from Genie.")

rows = pd.DataFrame(be.diagnoses())
if rows.empty:
    st.caption("No diagnoses yet.")
    st.stop()

rows["job"] = rows["run_name"].map(short_job)
cols = ["diagnosed_at", "job", "category", "summary", "fix_location", "fix_verified", "confidence", "status",
        "decided_by", "decided_at", "run_id"]
cats = st.multiselect("Category", sorted(rows["category"].dropna().unique()))
view = rows[rows["category"].isin(cats)] if cats else rows
st.dataframe(view[[c for c in cols if c in view.columns]], hide_index=True, use_container_width=True,
             column_config={"confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1),
                            "fix_verified": st.column_config.CheckboxColumn("verified")})
st.download_button("Download CSV", view.to_csv(index=False), "incidents.csv", "text/csv")
