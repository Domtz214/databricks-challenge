# Databricks notebook source
# MAGIC %md
# MAGIC # 08 · Harvey back-test (headline number)
# MAGIC Simulates a dispatch desk that can handle *C* reports/hour during Harvey and compares
# MAGIC **first-come-first-served** against the **Triage-ranked queue**. Priority uses only what was
# MAGIC known at arrival (classifier probability, text cues, forecast risk for the area at that hour).
# MAGIC
# MAGIC HumAID is a *sample* of the real tweet stream, so absolute minutes depend on C. Report a
# MAGIC sensitivity table across C values where the desk is overloaded at peak, not a single cherry-pick.

# COMMAND ----------

import os, sys, uuid
sys.path.append(os.path.abspath("../src"))
import mlflow, pandas as pd
from triage import taxonomy
from triage.backtest import QueuedReport, compare_policies
from triage.config import fq, H3_RES_AREA
from triage.scoring import IncidentSignals, urgency

mlflow.set_experiment(f"/Users/{spark.sql('select current_user()').first()[0]}/triage")

# COMMAND ----------

pdf = spark.sql(f"""
SELECT r.report_id, r.text, r.reported_at, r.lat, r.lon, p.p_life_threat, p.label,
       f.risk AS forecast_risk
FROM {fq('harvey_reports')} r
JOIN {fq('harvey_predictions')} p ON p.tweet_id = r.report_id
LEFT JOIN {fq('forecast_rescue_need')} f
  ON f.h3_area = h3_longlatash3string(r.lon, r.lat, {H3_RES_AREA})
 AND f.forecast_for = date_trunc('HOUR', r.reported_at)
""").toPandas()
# `label` already includes hand relabels (notebook 05). Ground truth = life-threatening.

def priority(row) -> float:
    s = IncidentSignals(
        p_life_threat=float(row.p_life_threat),
        vulnerable=taxonomy.is_vulnerable(row.text),
        people_count=taxonomy.extract_people_count(row.text),
        forecast_risk=None if pd.isna(row.forecast_risk) else float(row.forecast_risk),
        # TODO(you): add in_flood_warning from archived 2017 NWS alerts (IEM archive) by county + hour
    )
    return urgency(s).score

reports = [QueuedReport(r.report_id, r.reported_at.to_pydatetime(), priority(r),
                        r.label in taxonomy.LIFE_THREATENING) for r in pdf.itertuples()]
print(len(reports), "reports;", sum(r.life_threatening for r in reports), "life-threatening")

# COMMAND ----------

run_id = uuid.uuid4().hex[:8]
results = []
with mlflow.start_run(run_name="harvey_backtest"):
    for cap in [20, 40, 60, 120]:  # TODO(you): pick a range where peak hourly volume > capacity
        res = compare_policies(reports, cap)
        for policy in ("fifo", "triage"):
            for metric, value in res[policy].items():
                results.append((run_id, metric, policy, float(cap), float(value)))
        mlflow.log_metric(f"life_median_minutes_saved_c{cap}", res["life_median_minutes_saved"])
        print(cap, "/h ->", res)

out = spark.createDataFrame(results, "run_id STRING, metric STRING, policy STRING, capacity_per_hour DOUBLE, value DOUBLE")
out.selectExpr("*", "current_timestamp() AS created_at").write.mode("append").saveAsTable(fq("backtest_results"))
display(spark.table(fq("backtest_results")).filter(f"run_id = '{run_id}'"))
# Video line: "At 60 reports/hour, life-threatening reports were handled a median of X h sooner."
