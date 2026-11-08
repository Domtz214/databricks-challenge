import html
import json

import streamlit as st

from ui.backend import current_user, get_backend, incidents
from ui.components import (CATEGORY_TONE, STATUS_TONE, banner, chip, hero, lineage_dot, short_job, timeline,
                           unified_diff)
from ui.timeutil import ago

be = get_backend()
hero("Incidents", "Every failed run, diagnosed: root cause, blast radius, a verified fix.")

inc = incidents(be)
if not inc:
    st.success("No failed runs. Start a scenario in the 🧪 Chaos lab.")
    st.stop()

FILTERS = {"Open": ("new", "diagnosed"), "Resolved": ("fixed_and_rerun", "rerun", "dismissed"), "All": None}
choice = st.segmented_control("Show", list(FILTERS), default="Open", label_visibility="collapsed") or "Open"
shown = [i for i in inc if FILTERS[choice] is None or i["status"] in FILTERS[choice]]
ids = [i["run_id"] for i in shown]
if st.session_state.get("selected_run") not in ids:
    st.session_state.selected_run = ids[0] if ids else None

left, right = st.columns([1, 2.4], gap="large")

# ------------------------------------------------------------------ list
with left:
    if not shown:
        st.caption("Nothing here.")
    for i in shown:
        selected = i["run_id"] == st.session_state.selected_run
        with st.container(border=True):
            tags = chip(i["status"], STATUS_TONE.get(i["status"], "slate"))
            if i["dx"]:
                tags += chip(i["dx"].get("category", ""), CATEGORY_TONE.get(i["dx"].get("category"), "slate"))
            st.markdown(f"{'▶ ' if selected else ''}**{short_job(i['run_name'])}**<br>"
                        f"<span class='ld-muted'>{ago(i['start_time'])} · run {i['run_id']}</span><br>{tags}",
                        unsafe_allow_html=True)
            if not selected and st.button("View", key=f"view_{i['run_id']}", use_container_width=True):
                st.session_state.selected_run = i["run_id"]
                st.rerun()

# ------------------------------------------------------------------ detail
with right:
    cur = next((i for i in shown if i["run_id"] == st.session_state.selected_run), None)
    if cur is None:
        st.stop()
    dx = cur["dx"]
    title_tags = chip(cur["result_state"], "red") + chip(cur["status"], STATUS_TONE.get(cur["status"], "slate"))
    st.markdown(f"### {html.escape(short_job(cur['run_name']))}  {title_tags}", unsafe_allow_html=True)
    meta = f"run `{cur['run_id']}` · failed {ago(cur['start_time'])}"
    if cur.get("run_page_url"):
        meta += f" · [open run ↗]({cur['run_page_url']})"
    st.caption(meta)
    if cur.get("state_message"):
        st.code(cur["state_message"], language=None, wrap_lines=True)

    def run_diagnosis():
        with st.status("🩺 Investigating…", expanded=True) as s:
            def on_event(e):
                from ui.components import STEP_ICONS
                ico, label = STEP_ICONS.get(e["tool"], ("•", e["tool"]))
                s.write(f"{ico} {label} `{str(e.get('args', ''))[:90]}`")
            result, trace = be.diagnose(cur["run_id"], on_event=on_event)
            be.save_diagnosis(cur, result, trace)
            s.update(label=f"Diagnosis complete: {result.get('category', '')}", state="complete", expanded=False)
        st.rerun()

    if not dx:
        st.info("Not diagnosed yet. The doctor will read the error, the code, the tables it touches, their "
                "history and lineage, and verify any fix it proposes against the real source.")
        if st.button("🩺 Diagnose", type="primary"):
            run_diagnosis()
        st.stop()

    if "error" in dx:
        st.error(f"The doctor could not finish: {dx.get('error')}")
        if st.button("Retry diagnosis"):
            run_diagnosis()
        st.stop()

    tab_dx, tab_lin, tab_fix, tab_inv = st.tabs(["Diagnosis", "Lineage & blast radius", "Fix", "How it investigated"])

    with tab_dx:
        st.markdown(f"#### {html.escape(dx.get('summary', ''))}")
        culprit = dx.get("upstream_culprit") or {}
        if dx.get("fix_location") == "upstream_job" and culprit:
            banner(f"<b>🔺 Not this job's fault.</b> Upstream job <b>{html.escape(short_job(culprit.get('job_name')))}</b> "
                   f"changed <code>{html.escape(str(culprit.get('table')))}</code> in version {culprit.get('version')} "
                   f"({html.escape(str(culprit.get('timestamp')))}): <b>{html.escape(str(culprit.get('change')))}</b>", "red")
        if dx.get("blast_radius"):
            banner("<b>💥 Blast radius:</b> " + " ".join(chip(b.split('.')[-1], "amber") for b in dx["blast_radius"]),
                   "amber")
        a, b = st.columns([3, 1])
        with a:
            st.markdown(f"**Root cause.** {dx.get('root_cause', '')}")
            st.markdown("**Evidence**")
            for e in dx.get("evidence", []):
                st.markdown(f"- {e}")
            st.markdown(f"**Prevention.** {dx.get('prevention', '')}")
        with b:
            conf = float(dx.get("confidence") or 0)
            st.metric("Confidence", f"{conf:.0%}")
            st.progress(min(max(conf, 0.0), 1.0))
            st.markdown(chip(dx.get("category", ""), CATEGORY_TONE.get(dx.get("category"), "slate")) +
                        chip("fix upstream" if dx.get("fix_location") == "upstream_job" else "fix in place",
                             "red" if dx.get("fix_location") == "upstream_job" else "teal"), unsafe_allow_html=True)

    with tab_lin:
        edges = be.lineage_edges()
        table = culprit.get("table") if culprit else None
        st.graphviz_chart(lineage_dot(edges, focus=table, culprit_table=table, culprit_job=culprit.get("job_name"),
                                      blast=dx.get("blast_radius")), use_container_width=True)
        st.caption("🟥 changed upstream table · red edge = culprit job · 🟨 blast radius. Lineage parsed from job code + Delta history.")

    with tab_fix:
        fix = dx.get("fix") or {}
        if dx.get("fix_verified"):
            banner("<b>🛡️ Verified fix.</b> The exact code to replace was found once in the target notebook.", "green")
        elif fix.get("before"):
            banner(f"<b>⚠ Not verified.</b> {html.escape(str(dx.get('verification')))}", "amber")
        st.markdown(f"**{fix.get('description', '')}**  \n<span class='ld-muted ld-mono'>{html.escape(str(fix.get('notebook_path')))}</span>",
                    unsafe_allow_html=True)
        if fix.get("before") and fix.get("after"):
            try:
                diff = unified_diff(be.notebook_source(fix["notebook_path"]), fix["before"], fix["after"],
                                    fix["notebook_path"].split("/")[-1])
                st.code(diff or f"- {fix['before']}\n+ {fix['after']}", language="diff")
            except Exception:
                st.code(f"- {fix['before']}\n+ {fix['after']}", language="diff")
        if dx.get("genie_code_prompt"):
            st.markdown("**Prefer to edit it yourself?** Paste this into Genie Code in that notebook:")
            st.code(dx["genie_code_prompt"], language=None, wrap_lines=True)

        done = cur["status"] in ("fixed_and_rerun", "dismissed")
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("✅ Apply fix & rerun", type="primary", disabled=done or not dx.get("fix_verified"),
                     use_container_width=True):
            with st.status("Applying fix…", expanded=True) as s:
                for line in be.apply_fix(cur, dx):
                    s.write(line)
                s.update(label="Fix applied", state="complete")
            be.decide(cur, dx, "fixed_and_rerun", current_user())
            st.toast("Fix applied. Downstream rerun started.", icon="✅")
        if c2.button("🔁 Just rerun", disabled=done, use_container_width=True):
            be.rerun(cur["run_id"]); be.decide(cur, dx, "rerun", current_user()); st.toast("Rerun started", icon="🔁")
        if c3.button("✖ Dismiss", disabled=done, use_container_width=True):
            be.decide(cur, dx, "dismissed", current_user()); st.rerun()
        if c4.button("↻ Re-diagnose", use_container_width=True):
            run_diagnosis()

    with tab_inv:
        st.markdown("Every step the doctor took, in order. 🛡️ marks a fix it proposed that failed verification "
                    "and had to redo.")
        timeline(cur["trace"])
        with st.expander("Raw diagnosis JSON"):
            st.code(json.dumps(dx, indent=2), language="json")
