# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Rescue-need forecast ("predict before anyone asks")
# MAGIC Unit: H3 res-7 cell × hour. Target: ≥1 life-threatening report in the cell in the **next 6 h**.
# MAGIC Features available *at time t*: vulnerability (SVI), facilities in cell (nursing homes,
# MAGIC dialysis), nearest gauge stage / 3 h rise / ft above flood stage, recent reports in the cell and
# MAGIC its neighbors, hours since landfall, and (optional) elevation.
# MAGIC
# MAGIC Outputs: `forecast_features` (Features), MLflow run + UC model `rescue_forecast`,
# MAGIC `forecast_rescue_need` (read by the pipeline + app), `facility_lead_times` (**video number #2**).
# MAGIC
# MAGIC ⚠️ Labels are sparse (only geocoded tweets). If AUC is noisy, try res 6 cells, a 12 h horizon,
# MAGIC or add a second label source (see TODO at the bottom).

# COMMAND ----------

import os, sys
sys.path.append(os.path.abspath("../src"))
import mlflow, numpy as np, pandas as pd
from pyspark.sql import functions as F
from triage import config, taxonomy
from triage.config import fq, H3_RES_AREA

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(f"/Users/{spark.sql('select current_user()').first()[0]}/triage")

START, END, HORIZON_H = "2017-08-25 00:00:00", "2017-09-01 00:00:00", 6
BBOX = dict(lat_min=29.35, lat_max=30.25, lon_min=-95.95, lon_max=-94.85)  # greater Houston

# COMMAND ----------

# MAGIC %md ## 1. Grid × hours and labels

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW life_reports AS
SELECT h3_longlatash3string(lon, lat, {H3_RES_AREA}) AS h3_area, date_trunc('HOUR', reported_at) AS hr
FROM {fq('harvey_reports')}
WHERE lat IS NOT NULL AND ground_truth_label IN ({", ".join(f"'{c}'" for c in taxonomy.LIFE_THREATENING)})
""")

spark.sql(f"""
CREATE OR REPLACE TEMP VIEW grid AS
SELECT a.h3_area, a.svi_percentile, h.hr
FROM (SELECT DISTINCT r.h3_area, r.svi_percentile FROM {fq('ref_h3_area')} r
      WHERE h3_centerasgeojson(r.h3_area):coordinates[1]::double BETWEEN {BBOX['lat_min']} AND {BBOX['lat_max']}
        AND h3_centerasgeojson(r.h3_area):coordinates[0]::double BETWEEN {BBOX['lon_min']} AND {BBOX['lon_max']}) a
CROSS JOIN (SELECT explode(sequence(timestamp'{START}', timestamp'{END}', INTERVAL 1 HOUR)) AS hr) h
""")
print(spark.table("grid").count(), "cell-hours")

# COMMAND ----------

# MAGIC %md ## 2. Features at time t (no peeking past t)

# COMMAND ----------

feat = spark.sql(f"""
WITH counts AS (SELECT h3_area, hr, count(*) n FROM life_reports GROUP BY 1, 2),
cell_hist AS (
  SELECT g.h3_area, g.hr,
         coalesce(sum(CASE WHEN c.hr >  g.hr - INTERVAL 6 HOURS AND c.hr <= g.hr THEN c.n END), 0) AS reports_prev_6h,
         coalesce(sum(CASE WHEN c.hr >  g.hr AND c.hr <= g.hr + INTERVAL {HORIZON_H} HOURS THEN c.n END), 0) AS reports_next_h
  FROM grid g LEFT JOIN counts c ON c.h3_area = g.h3_area AND c.hr BETWEEN g.hr - INTERVAL 6 HOURS AND g.hr + INTERVAL {HORIZON_H} HOURS
  GROUP BY 1, 2),
nbr AS (
  SELECT g.h3_area, g.hr, coalesce(sum(c.n), 0) AS nbr_reports_prev_6h
  FROM grid g LATERAL VIEW explode(h3_kring(g.h3_area, 1)) k AS nb
  LEFT JOIN counts c ON c.h3_area = k.nb AND c.hr > g.hr - INTERVAL 6 HOURS AND c.hr <= g.hr
  GROUP BY 1, 2),
fac AS (
  SELECT h3_area, count_if(kind = 'nursing_home') AS n_nursing_homes, count_if(kind = 'hospital') AS n_hospitals
  FROM {fq('ref_facilities')} GROUP BY 1)
SELECT g.h3_area, g.hr, g.svi_percentile,
       h.reports_prev_6h, n.nbr_reports_prev_6h,
       coalesce(f.n_nursing_homes, 0) AS n_nursing_homes, coalesce(f.n_hospitals, 0) AS n_hospitals,
       (unix_timestamp(g.hr) - unix_timestamp(timestamp'{START}')) / 3600.0 AS hours_since_start,
       CAST(h.reports_next_h > 0 AS INT) AS label
FROM grid g JOIN cell_hist h USING (h3_area, hr) JOIN nbr n USING (h3_area, hr)
LEFT JOIN fac f USING (h3_area)
""")

# Gauge features: nearest gauge per area cell, hourly stage from harvey_gauges (notebook 04 backfill).
# TODO(you): after running the backfill, uncomment and join. Pattern:
#   hourly = spark.sql(f"SELECT site_id, date_trunc('HOUR', observed_at) hr, max(stage_ft) stage_ft FROM {fq('harvey_gauges')} GROUP BY 1,2")
#   nearest_site per h3_area: explode h3_kring(gauge_cell, 3), min h3_distance (same as notebook 01)
#   rise_3h = stage_ft - lag(stage_ft, 3) over (partition by site_id order by hr)
#   ft_above_flood = stage_ft - flood_stage_ft (ref_gauge_flood_stage)
# TODO(you) optional: elevation per cell via USGS EPQS (https://epqs.nationalmap.gov/v1/json?x=LON&y=LAT&units=Feet)
#   cached into ref_h3_elevation; low-lying cells are a strong prior.

feat.write.mode("overwrite").saveAsTable(fq("forecast_features"))
# TODO(you) optional: register forecast_features as a feature table (Features page) with
# primary keys (h3_area, hr) to show Feature Engineering in Unity Catalog.

# COMMAND ----------

# MAGIC %md ## 3. Train with a time split

# COMMAND ----------

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, roc_auc_score

pdf = spark.table(fq("forecast_features")).toPandas().sort_values("hr")
FEATURES = [c for c in pdf.columns if c not in ("h3_area", "hr", "label")]
cut = pdf.hr.quantile(0.6)
tr, te = pdf[pdf.hr <= cut], pdf[pdf.hr > cut]
print("positives train/test:", tr.label.sum(), te.label.sum())

with mlflow.start_run(run_name="rescue_forecast_hgb") as run:
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, class_weight="balanced")
    clf.fit(tr[FEATURES].fillna(-1), tr.label)
    p = clf.predict_proba(te[FEATURES].fillna(-1))[:, 1]
    te = te.assign(risk=p)
    # precision@k: of the top-k riskiest cells each hour, how many really got a rescue request?
    p_at_k = te.sort_values("risk", ascending=False).groupby("hr").head(20).label.mean()
    mlflow.log_params({"horizon_h": HORIZON_H, "res": H3_RES_AREA, "features": ",".join(FEATURES)})
    mlflow.log_metrics({"roc_auc": roc_auc_score(te.label, p), "avg_precision": average_precision_score(te.label, p),
                        "precision_at_20_per_hour": p_at_k, "base_rate": te.label.mean()})
    mlflow.sklearn.log_model(clf, "model", input_example=te[FEATURES].fillna(-1).head(3),
                             registered_model_name=fq("rescue_forecast"))
    run_id = run.info.run_id

# COMMAND ----------

# MAGIC %md ## 4. Write forecasts + facility lead times

# COMMAND ----------

pdf["risk"] = clf.predict_proba(pdf[FEATURES].fillna(-1))[:, 1]
fc = pd.DataFrame({"h3_area": pdf.h3_area, "forecast_for": pdf.hr, "horizon_h": HORIZON_H,
                   "risk": pdf.risk, "model_version": run_id, "created_at": pd.Timestamp.utcnow().tz_localize(None)})
spark.createDataFrame(fc).write.mode("overwrite").saveAsTable(fq("forecast_rescue_need"))

THRESH = 0.5  # TODO(you): pick from the PR curve (e.g. threshold at ~precision 0.5)
spark.sql(f"""
CREATE OR REPLACE TABLE {fq('facility_lead_times')} AS
WITH flagged AS (SELECT h3_area, min(forecast_for) first_flagged_at FROM {fq('forecast_rescue_need')}
                 WHERE risk >= {THRESH} GROUP BY 1),
first_rep AS (SELECT h3_area, min(hr) first_report_at FROM life_reports GROUP BY 1)
SELECT f.facility_id, f.name, f.kind, f.h3_area, fl.first_flagged_at, r.first_report_at,
       (unix_timestamp(r.first_report_at) - unix_timestamp(fl.first_flagged_at)) / 3600.0 AS lead_time_h
FROM {fq('ref_facilities')} f
JOIN first_rep r USING (h3_area) LEFT JOIN flagged fl USING (h3_area)
WHERE f.kind IN ('nursing_home', 'hospital', 'dialysis')
""")
display(spark.sql(f"""SELECT count(*) facilities_with_rescue_reports,
                             count_if(lead_time_h > 0) flagged_before_first_report,
                             percentile(lead_time_h, 0.5) median_lead_h
                      FROM {fq('facility_lead_times')}"""))

# TODO(you) label alternatives if geocoded tweets are too sparse:
#   - OpenFEMA Individual Assistance registrations (Harvey, DR-4332) by ZIP: flood damage ground truth
#     (no timestamps -> use as a static prior / extra feature, not the label)
#   - Houston 311 flooding requests, if you can find the Aug 2017 extract
