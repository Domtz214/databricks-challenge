# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Replay a storm into the landing zone
# MAGIC Writes reports as JSON files into `landing/reports` at accelerated speed, so the pipeline and
# MAGIC map look live. `mode=harvey` replays real tweets (original timestamps kept; the pipeline's
# MAGIC `now_mode=replay` uses the latest report time as "now"). `mode=synthetic` generates bilingual
# MAGIC reports. Optionally triggers a pipeline update every N batches.

# COMMAND ----------

dbutils.widgets.dropdown("mode", "harvey", ["harvey", "synthetic"])
dbutils.widgets.text("start", "2017-08-26T00:00:00Z")
dbutils.widgets.text("end", "2017-08-30T00:00:00Z")
dbutils.widgets.text("sim_minutes_per_batch", "60")   # storm time per file
dbutils.widgets.text("seconds_between_batches", "5")  # wall time per file (60 min / 5 s = 720x)
dbutils.widgets.text("trigger_pipeline_every", "6")   # batches; 0 = never
dbutils.widgets.text("pipeline_name", "triage-pipeline")

# COMMAND ----------

import json, os, sys, time, uuid
from datetime import datetime, timedelta, timezone
sys.path.append(os.path.abspath("../src"))
from triage import config
from triage.config import fq
from triage.replay import synthetic_reports

mode = dbutils.widgets.get("mode")
start = datetime.fromisoformat(dbutils.widgets.get("start").replace("Z", "+00:00"))
end = datetime.fromisoformat(dbutils.widgets.get("end").replace("Z", "+00:00"))
step = timedelta(minutes=int(dbutils.widgets.get("sim_minutes_per_batch")))
pause = float(dbutils.widgets.get("seconds_between_batches"))
trigger_every = int(dbutils.widgets.get("trigger_pipeline_every"))

# COMMAND ----------

def trigger_pipeline():
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    name = dbutils.widgets.get("pipeline_name")
    match = [p for p in w.pipelines.list_pipelines() if name in (p.name or "")]
    if not match:
        print("pipeline not found:", name); return
    try:
        w.pipelines.start_update(match[0].pipeline_id)
    except Exception as e:  # an update is already running -> fine
        print("start_update:", e)

def write_batch(rows):
    if not rows:
        return
    path = f"{config.LANDING_REPORTS}/{mode}_{uuid.uuid4().hex[:10]}.json"
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r, default=str) for r in rows))

# COMMAND ----------

if mode == "harvey":
    pdf = (spark.table(fq("harvey_reports"))
           .filter(f"reported_at >= '{start.isoformat()}' AND reported_at < '{end.isoformat()}'")
           .orderBy("reported_at").toPandas())
    print(len(pdf), "reports to replay")

t, i = start, 0
while t < end:
    if mode == "harvey":
        batch = pdf[(pdf.reported_at >= t.replace(tzinfo=None)) & (pdf.reported_at < (t + step).replace(tzinfo=None))]
        rows = batch.astype(object).where(batch.notna(), None).to_dict("records")  # NaN -> null
    else:
        rows = synthetic_reports(20, center=config.DEFAULT_CENTER, start=t,
                                 minutes_span=int(step.total_seconds() // 60))
    write_batch(rows)
    i += 1
    print(f"{t:%Y-%m-%d %H:%M} -> {len(rows)} reports")
    if trigger_every and i % trigger_every == 0:
        trigger_pipeline()
    t += step
    time.sleep(pause)
