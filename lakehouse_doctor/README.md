# 🩺 Lakehouse Doctor (pivot candidate, basic scaffold)

An AI reliability + cost engineer that lives in a Databricks workspace. It notices failed jobs,
**investigates like a senior engineer** (error, stack trace, the notebook's code, table metadata, run
history), explains the root cause in plain English, proposes a code fix, and, **with a human's
approval**, patches the notebook and reruns the job. A cost tab reads `system.billing` /
`system.query` to find what's burning money.

No external data: the patient is your own workspace.

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
  tests/                    offline tests (fake LLM)
```

## Try it (about 15 min)
1. Run `notebooks/99_update_from_github` (repo root) to pull this folder into your workspace.
2. Run `lakehouse_doctor/notebooks/00_setup_and_break_things`, then wait about 2 min for the 3 chaos jobs to fail.
3. Run `lakehouse_doctor/notebooks/01_try_the_doctor`: read the diagnoses.
4. (Optional) Compute → Apps → Create → custom app from `lakehouse_doctor/app`, add the
   **SQL warehouse** resource with key `sql-warehouse`.

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
