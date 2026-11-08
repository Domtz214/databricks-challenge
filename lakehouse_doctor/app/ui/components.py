"""Visual building blocks: theme CSS, KPI tiles, chips, banners, timeline, lineage graph, diffs."""

from __future__ import annotations

import difflib
import html

import streamlit as st

TONES = {
    "red": ("#F87171", "rgba(248,113,113,.12)"),
    "amber": ("#FBBF24", "rgba(251,191,36,.12)"),
    "green": ("#34D399", "rgba(52,211,153,.12)"),
    "teal": ("#2DD4BF", "rgba(45,212,191,.12)"),
    "blue": ("#60A5FA", "rgba(96,165,250,.12)"),
    "violet": ("#A78BFA", "rgba(167,139,250,.12)"),
    "slate": ("#94A3B8", "rgba(148,163,184,.12)"),
}

CSS = """
<style>
  .block-container {padding-top: 1.6rem; max-width: 1400px;}
  h1, h2, h3 {letter-spacing: -0.01em;}
  .ld-hero {display:flex; justify-content:space-between; align-items:flex-end; margin-bottom: .6rem;}
  .ld-hero h1 {font-size: 1.9rem; margin: 0;}
  .ld-hero p {color:#94A3B8; margin:.2rem 0 0 0;}
  .ld-card {background:#121B2E; border:1px solid #1E2A44; border-radius:14px; padding:16px 18px; height:100%;}
  .ld-kpi-label {color:#94A3B8; font-size:.72rem; text-transform:uppercase; letter-spacing:.09em; font-weight:600;}
  .ld-kpi-value {font-size:1.9rem; font-weight:750; margin-top:.15rem; line-height:1.1;}
  .ld-kpi-sub {color:#64748B; font-size:.78rem; margin-top:.25rem;}
  .ld-chip {display:inline-block; padding:2px 10px; border-radius:999px; font-size:.72rem; font-weight:650;
            margin-right:6px; white-space:nowrap;}
  .ld-banner {border-radius:12px; padding:12px 16px; margin:.4rem 0 .8rem 0; border:1px solid;}
  .ld-mono {font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.85rem;}
  .ld-step {display:flex; gap:10px; align-items:flex-start; padding:6px 0; border-bottom:1px dashed #1E2A44;}
  .ld-step:last-child {border-bottom:none;}
  .ld-step .ico {width:26px; text-align:center;}
  .ld-step .name {font-weight:650;}
  .ld-step .det {color:#94A3B8; font-size:.8rem; word-break:break-all;}
  .ld-muted {color:#94A3B8;}
  div[data-testid="stMetric"] {background:#121B2E; border:1px solid #1E2A44; border-radius:14px; padding:12px 16px;}
</style>
"""

STEP_ICONS = {
    "run_error": ("🧾", "Read the error + stack trace"),
    "source_code": ("📄", "Read notebook source"),
    "table_info": ("🗂️", "Checked table metadata"),
    "lineage": ("🕸️", "Mapped lineage + blast radius"),
    "table_history": ("🕰️", "Read table version history"),
    "schema_diff": ("🧬", "Compared table versions"),
    "run_details": ("🔎", "Inspected the upstream run"),
    "job_history": ("📈", "Checked job run history"),
    "ask_genie": ("🧞", "Asked Genie"),
    "verify_fix": ("🛡️", "Verification rejected a fix, retrying"),
}

STATUS_TONE = {"new": "red", "diagnosed": "amber", "fixed_and_rerun": "green", "rerun": "blue",
               "dismissed": "slate"}
CATEGORY_TONE = {"schema_drift": "violet", "value_drift": "violet", "missing_table": "blue",
                 "data_quality": "amber", "code_bug": "red", "flaky": "slate"}


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def chip(text: str, tone: str = "slate") -> str:
    fg, bg = TONES.get(tone, TONES["slate"])
    return f'<span class="ld-chip" style="color:{fg};background:{bg};border:1px solid {fg}33">{html.escape(str(text))}</span>'


def hero(title: str, subtitle: str, right_html: str = "") -> None:
    st.markdown(f'<div class="ld-hero"><div><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>'
                f"<div>{right_html}</div></div>", unsafe_allow_html=True)


def kpi(label: str, value, sub: str = "", tone: str | None = None) -> None:
    color = TONES[tone][0] if tone else "#E2E8F0"
    st.markdown(f'<div class="ld-card"><div class="ld-kpi-label">{html.escape(label)}</div>'
                f'<div class="ld-kpi-value" style="color:{color}">{html.escape(str(value))}</div>'
                f'<div class="ld-kpi-sub">{html.escape(sub)}</div></div>', unsafe_allow_html=True)


def banner(body_html: str, tone: str = "red") -> None:
    fg, bg = TONES[tone]
    st.markdown(f'<div class="ld-banner" style="background:{bg};border-color:{fg}55;border-left:4px solid {fg}">'
                f"{body_html}</div>", unsafe_allow_html=True)


def timeline(trace: list[dict]) -> None:
    rows = []
    for t in trace:
        ico, label = STEP_ICONS.get(t.get("tool"), ("•", t.get("tool", "")))
        detail = t.get("args") or t.get("result_preview") or ""
        rows.append(f'<div class="ld-step"><div class="ico">{ico}</div><div><div class="name">{html.escape(label)}</div>'
                    f'<div class="det ld-mono">{html.escape(str(detail)[:160])}</div></div></div>')
    st.markdown("".join(rows) or '<span class="ld-muted">No steps recorded.</span>', unsafe_allow_html=True)


def short_job(name: str | None) -> str:
    return (name or "").replace("doctor-chaos-", "")


def lineage_dot(edges: list[dict], focus: str | None = None, culprit_table: str | None = None,
                culprit_job: str | None = None, blast: list[str] | None = None) -> str:
    """Graphviz DOT: tables as nodes, edges labelled by the job that moves data between them.
    culprit table/job in red, blast radius in amber, focus table outlined in teal."""
    blast = {b.lower() for b in (blast or [])}
    culprit_table = (culprit_table or "").lower()
    focus = (focus or "").lower()
    nodes, lines = set(), []
    for e in edges:
        src, tgt = e.get("source"), e.get("target") or f"[{short_job(e.get('job_name'))}]"
        nodes.update(x for x in (src, tgt) if x)
        if not src:
            continue
        is_culprit = culprit_job and short_job(e.get("job_name")) == short_job(culprit_job)
        color = "#F87171" if is_culprit else "#475569"
        lines.append(f'  "{src}" -> "{tgt}" [label="{short_job(e.get("job_name"))}", color="{color}", '
                     f'fontcolor="{color}", penwidth={2.4 if is_culprit else 1.2}];')
    node_lines = []
    for n in sorted(nodes):
        label = n.split(".")[-1] if "." in n else n
        if n.lower() == culprit_table:
            style = 'fillcolor="#3B1219", color="#F87171", fontcolor="#FECACA"'
        elif n.lower() in blast:
            style = 'fillcolor="#3A2A0B", color="#FBBF24", fontcolor="#FDE68A"'
        elif n.lower() == focus:
            style = 'fillcolor="#0F2E2B", color="#2DD4BF", fontcolor="#CCFBF1"'
        else:
            style = 'fillcolor="#121B2E", color="#334155", fontcolor="#CBD5E1"'
        shape = "note" if n.startswith("[") else "box"   # "[job]" = a reader with no output table
        node_lines.append(f'  "{n}" [label="{label}", shape={shape}, style="rounded,filled", {style}];')
    return ("digraph G {\n  rankdir=LR; bgcolor=\"transparent\"; nodesep=0.4; ranksep=0.9;\n"
            '  node [fontname="Helvetica", fontsize=11, penwidth=1.4];\n'
            '  edge [fontname="Helvetica", fontsize=9, arrowsize=0.7];\n'
            + "\n".join(node_lines + lines) + "\n}")


def unified_diff(code: str, before: str, after: str, path: str = "notebook") -> str:
    patched = code.replace(before, after, 1)
    return "\n".join(difflib.unified_diff(code.splitlines(), patched.splitlines(),
                                          fromfile=f"{path} (current)", tofile=f"{path} (fixed)", lineterm="", n=3))
