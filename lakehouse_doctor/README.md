# 🩺 Lakehouse Doctor (pivot candidate, basic scaffold)

An AI reliability + cost engineer that lives in a Databricks workspace. It notices failed jobs,
**investigates like a senior engineer** (error, stack trace, the notebook's code, table metadata, run
history), explains the root cause in plain English, proposes a code fix, and, **with a human's
approval**, patches the notebook and reruns the job. A cost tab reads `system.billing` /
`system.query` to find what's burning money.

No external data: the patient is your own workspace.

## How it differs from the built-in Assistant / Genie Code
| | Built-in "Diagnose error" / Genie Code | Lakehouse Doctor |
|---|---|---|
| Scope | the notebook + error in front of you | the whole workspace: jobs, tables, upstream and downstream |
| Root cause | assumes it's in the failing code | traces **upstream** via code lineage + Delta table history (`DESCRIBE HISTORY`, version-to-version schema/value diff) |
| Impact | none | **blast radius**: downstream tables and jobs |
| Fix | suggests code in place | fixes in the **right place** (upstream if needed), patches with backup, reruns the chain in order |
| Memory / ops | none | incident log → Genie space; cost of failures from system tables |

**It works *with* them, not against them:**
- **Genie (data Q&A):** the doctor calls a Genie space as a tool mid-investigation ("average o_totalprice per day
  this week?"), and humans chat with the same space in the app's *Ask Genie* tab. Genie owns trusted NL→SQL;
  the doctor owns orchestration across Jobs API, code, lineage and history.
- **Genie Code (code edits):** every diagnosis includes a ready-to-paste `genie_code_prompt` for the right
  notebook, so the built-in editor implements the fix where it's strongest.

Pitch: *"Genie Code fixes the line in front of you. Lakehouse Doctor finds the incident behind it, then hands
Genie Code the right file."*

## The demo loop (what the video shows)
1. Three jobs fail in different realistic ways (`chaos/`): table-name typo, upstream schema drift, bad data
   (divide by zero).
2. The app lists them → **Diagnose** → the doctor calls tools (visible trace), returns root cause +
   evidence + before/after fix.
3. **Apply fix + rerun** → notebook patched (backup kept) → job repaired → green.
4. Cost tab: top DBU consumers and slowest queries from system tables.

## Layout
```
lakehouse_doctor/
  app/                      Databricks App (deploy this folder)
    app.py                  Streamlit UI: Incidents · Cost · History
    doctor/agent.py         tool-calling diagnosis loop (Foundation Model API)
    doctor/signals.py       Jobs API (live failures, errors, code, rerun, patch) + system-table queries
    doctor/db.py            Spark / SQL-warehouse query helpers
  chaos/                    3 deliberately broken notebooks
  notebooks/00_setup_and_break_things   schema + sample data + creates & runs the chaos jobs
  notebooks/01_try_the_doctor           run diagnoses from a notebook (no app needed)
  notebooks/02_upstream_drift_scenario  3-job chain; upstream "succeeds" but drifts; downstream breaks
  notebooks/03_genie_space_setup        lineage table + system-table views + Genie space instructions
    doctor/lineage.py       code lineage, blast radius, DESCRIBE HISTORY forensics, schema/value diff
    doctor/genie.py         Genie Conversation API (as a doctor tool + chat tab)
  tests/                    offline tests (fake LLM)
```

## The app (one-stop shop)
| Page | What it does |
|---|---|
| 🩺 **Overview** | KPIs (open incidents, 7-day job health, verified fixes, root-cause-upstream count, spend on failed runs), run-outcome chart, "needs attention" queue, *Diagnose all new* |
| 🚨 **Incidents** | List + detail. **Diagnose** streams the investigation live; tabs: Diagnosis (culprit + blast-radius banners, evidence, confidence) · Lineage (graph with culprit in red, blast radius in amber) · Fix (verification badge, real unified diff, Genie Code prompt, *Apply fix & rerun*) · How it investigated (step timeline) |
| 🕸️ **Lineage explorer** | Any table: writers, upstream, blast radius, Delta version history, version-to-version schema/value diff |
| 🧞 **Ask Genie** | Chat with the Genie space the doctor also uses; shows Genie's SQL |
| 💸 **Costs** | DBUs/$ by resource, spend on failed runs, slowest queries (system tables) |
| 📜 **Incident log** | Every diagnosis + human decision (Delta), CSV export |
| 🧪 **Chaos lab** | One-click scenarios that break real jobs (typo, bad append, bad data, silent upstream drift, reset) |

Screenshots (demo data): `docs/screenshots/`.

Preview anywhere with sample data: `cd lakehouse_doctor/app && DOCTOR_FAKE=1 streamlit run app.py`.

## Deploy on Databricks (about 15 min)
1. Run `notebooks/99_update_from_github` (repo root) to pull the latest code.
2. Run `lakehouse_doctor/notebooks/00_setup_and_break_things` and `02_upstream_drift_scenario` once (creates the jobs).
   Optional: `03_genie_space_setup` → create the Genie space → put its ID in `app/app.yaml`.
3. **Compute → Apps → Create app → Custom**, name it `lakehouse-doctor`.
4. In the app's settings → **Resources → Add → SQL warehouse** → *Serverless Starter Warehouse*, permission *Can use*,
   resource key **`sql-warehouse`** (must match `app.yaml`).
5. **Deploy** with source path `/Workspace/Users/<you>/databricks-challenge/lakehouse_doctor/app`.
6. Run `lakehouse_doctor/notebooks/04_grant_app_permissions` (widget `app_name` = `lakehouse-doctor`). The app runs
   as its own service principal; this grants it the jobs, notebooks folder, warehouse, schema and system tables.
7. Open the app URL. Chaos lab → *Silent upstream drift* → wait ~1 min → Incidents → **Diagnose**.

After code changes: run `99_update_from_github`, then **Deploy** again on the app page.

## How it could level up (if we commit)
- **Proactive mode:** a scheduled job checks for new failures every few minutes and posts a diagnosis
  before anyone opens the app (Alerts / email).
- **Pattern memory:** incidents stored (Lakebase or Delta) + embeddings → "this looks like the
  schema-drift incident from last Tuesday; the fix then was…".
- **Cost doctor:** rewrite the slowest query, run both versions, report measured speedup and $/month saved.
- **Pipeline (Lakeflow) failures + data-quality expectation failures**, not just jobs.
- **Eval harness:** a library of 20+ chaos scenarios with known root causes → diagnosis accuracy %,
  fix success rate %, mean time to resolution vs. a human (the headline numbers).
- **Genie space** over incidents + billing for "what broke most this month and what did it cost?"
