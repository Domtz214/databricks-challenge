# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Poll live feeds (NWS alerts + USGS gauges)
# MAGIC Scheduled every 10 min by the `triage-live-feeds` job (databricks.yml). Writes JSON lines into
# MAGIC the landing volume; Auto Loader in the pipeline picks them up. This is the **real, live** data
# MAGIC in the video ("running on today's Texas data").
# MAGIC
# MAGIC Also contains a one-off **Harvey gauge backfill** (Aug 2017) for notebooks 06/08.

# COMMAND ----------

import json, os, sys, uuid
from datetime import datetime, timezone
sys.path.append(os.path.abspath("../src"))
from triage import config, feeds
from triage.config import fq

def write_jsonl(rows, folder, prefix):
    if not rows:
        print(prefix, "no rows"); return
    path = f"{folder}/{prefix}_{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}.json"
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in rows))
    print(prefix, len(rows), "->", path)

# COMMAND ----------

alerts = feeds.fetch_nws_alerts("TX")
write_jsonl(alerts, config.LANDING_ALERTS, "nws")

# Statewide gage height for the last 3h. TODO(you): if the response is too large/slow, pass
# sites=[...] for just the gauges in ref_gauge_flood_stage.
gauges = feeds.fetch_usgs_gauges(state="tx", period="PT3H")
write_jsonl(gauges, config.LANDING_GAUGES, "usgs")

# COMMAND ----------

# MAGIC %md ### One-off: Harvey gauge history → `harvey_gauges` (not through the pipeline)
# MAGIC Set `RUN_BACKFILL = True` once.

# COMMAND ----------

RUN_BACKFILL = False
if RUN_BACKFILL:
    sites = [r.site_id for r in spark.table(fq("ref_gauge_flood_stage")).select("site_id").collect()]
    rows = feeds.fetch_usgs_gauges(sites=sites, start="2017-08-24T00:00:00Z", end="2017-09-02T00:00:00Z", period=None)
    df = (spark.createDataFrame(rows)
          .selectExpr("site_id", "site_name", "lat", "lon", "value AS stage_ft",
                      "to_timestamp(observed_at) AS observed_at"))
    df.write.mode("overwrite").saveAsTable(fq("harvey_gauges"))
    display(spark.table(fq("harvey_gauges")).groupBy("site_name").count())
