# Databricks notebook source
# MAGIC %md
# MAGIC # 09 · Agent evaluation + traces
# MAGIC Runs the dispatcher agent on the top open incidents, checks structural rules, uses an LLM judge
# MAGIC for groundedness, and logs everything to MLflow (traces show every tool call — great B-roll).
# MAGIC
# MAGIC Run after a replay has filled `gold_priority_queue`.

# COMMAND ----------

# MAGIC %pip install -q openai
# MAGIC %restart_python

# COMMAND ----------

import json, os, sys
sys.path.append(os.path.abspath("../src"))
import mlflow, pandas as pd
from triage import config
from triage.agent import TriageAgent
from triage.agent.tools import top_incidents
from triage.db import SparkSql

mlflow.set_experiment(f"/Users/{spark.sql('select current_user()').first()[0]}/triage")
db = SparkSql(spark)
agent = TriageAgent(db)

# COMMAND ----------

incidents = top_incidents(db, 15)
recs = []
with mlflow.start_run(run_name="agent_eval"):
    for inc in incidents:
        rec = agent.recommend(inc["incident_id"])
        recs.append({"incident": inc, "rec": rec})

    def ok(r):
        rec = r["rec"]
        return {
            "valid_json": "error" not in rec,
            "has_citation": bool(rec.get("citations")),
            "priority_matches_tier": rec.get("priority") == r["incident"]["tier"],
            "has_reporter_reply": bool((rec.get("reporter_reply") or {}).get("text")),
        }
    checks = pd.DataFrame([ok(r) for r in recs])
    mlflow.log_metrics({f"rate_{c}": float(checks[c].mean()) for c in checks.columns})

    # LLM judge: is every reasoning bullet supported by the incident data / tool results?
    # TODO(you): swap for mlflow.genai.evaluate with built-in scorers (Guidelines / RetrievalGroundedness)
    # if your MLflow version has them; this hand-rolled judge works everywhere.
    judge_scores = []
    for r in recs:
        prompt = ("Score 1-5 how well this dispatch recommendation is justified by the incident data. "
                  "Penalize invented facts. Reply with only the number.\n\n"
                  f"INCIDENT: {json.dumps(r['incident'], default=str)}\n\nRECOMMENDATION: {json.dumps(r['rec'])}")
        resp = agent.client.chat.completions.create(model=config.LLM_ENDPOINT,
                                                    messages=[{"role": "user", "content": prompt}])
        try:
            judge_scores.append(float(resp.choices[0].message.content.strip()[:1]))
        except ValueError:
            pass
    if judge_scores:
        mlflow.log_metric("judge_groundedness_mean", sum(judge_scores) / len(judge_scores))
    mlflow.log_text(json.dumps(recs, default=str, indent=2), "recommendations.json")

display(checks)
