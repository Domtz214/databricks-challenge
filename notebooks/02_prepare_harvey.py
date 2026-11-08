# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Prepare Hurricane Harvey data (HumAID)
# MAGIC 1. Load HumAID Harvey tweets (ID, text, label), recover timestamps from tweet IDs.
# MAGIC 2. Map labels to the Triage taxonomy.
# MAGIC 3. Extract locations with `ai_extract` and geocode → `harvey_reports` (the replay source).
# MAGIC 4. Export a sample for hand relabeling (rescue vs supplies) → your evaluation set.
# MAGIC
# MAGIC Download: HumAID event files for `hurricane_harvey_2017` (train/dev/test TSV) →
# MAGIC `reference/humaid/`. See `docs/data_sources.md`.

# COMMAND ----------

import os, sys
SRC = os.path.abspath("../src")
sys.path.append(SRC)
from pyspark.sql import functions as F
from triage import config, taxonomy
from triage.config import fq

# COMMAND ----------

raw = (spark.read.option("header", True).option("sep", "\t").option("quote", "")
       .csv(f"{config.REFERENCE_DIR}/humaid/hurricane_harvey_2017_*.tsv")
       .withColumn("split", F.regexp_extract(F.col("_metadata.file_path"), r"_(train|dev|test)\.tsv", 1)))
display(raw.limit(5))

# Tweet IDs are Snowflake IDs: (id >> 22) + Twitter epoch = creation time in ms
# (same math as triage.replay.tweet_id_to_datetime, done in SQL so no UDF is needed).
to_ts = lambda c: F.expr(f"timestamp_millis(shiftright(cast({c} AS BIGINT), 22) + 1288834974657)")
label_map = F.create_map(*[F.lit(x) for kv in taxonomy.HUMAID_TO_TRIAGE.items() for x in kv])

tweets = (raw.select(F.col("tweet_id").cast("string"), F.col("tweet_text").alias("text"),
                     F.col("class_label").alias("humaid_label"), "split")
          .withColumn("reported_at", to_ts("tweet_id"))
          .withColumn("label", label_map[F.col("humaid_label")])
          .withColumn("life_threatening", F.col("label").isin(*taxonomy.LIFE_THREATENING)))
tweets.write.mode("overwrite").saveAsTable(fq("harvey_tweets"))
display(spark.table(fq("harvey_tweets")).groupBy("label").count().orderBy(F.desc("count")))

# COMMAND ----------

# MAGIC %md ### Location extraction (AI Functions, batch) + geocoding
# MAGIC Runs `ai_extract` once over relevant tweets (not `not_relevant`). Cost scales with rows:
# MAGIC start with `LIMIT 2000`, then remove the limit.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TABLE {fq('harvey_locations')} AS
SELECT tweet_id, ai_extract(text, array('location', 'street', 'neighborhood', 'zip')) AS loc
FROM {fq('harvey_tweets')}
WHERE label <> 'not_relevant'
LIMIT 2000  -- TODO(you): remove once you've checked cost/quality
""")
display(spark.sql(f"""SELECT loc.location, count(*) n FROM {fq('harvey_locations')}
                      WHERE loc.location IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 50"""))
# ^ Use this list to extend the gazetteer in notebook 01.

# COMMAND ----------

gaz_rows = spark.table(fq("ref_gazetteer")).collect()
places = [(r["name"], r["lat"], r["lon"]) for r in gaz_rows]

@F.pandas_udf("struct<lat:double, lon:double>")
def geocode(loc_text, text):
    import sys
    import pandas as pd
    if SRC not in sys.path:
        sys.path.append(SRC)   # UDFs run on workers
    from triage.geo import Gazetteer, Place
    g = Gazetteer([Place(n, a, o) for n, a, o in places])
    out = []
    for l, t in zip(loc_text, text):
        p = g.match(f"{l or ''} {t or ''}")
        out.append((p.lat, p.lon) if p else (None, None))
    return pd.DataFrame(out, columns=["lat", "lon"])
# TODO(you) optional: for strings that look like street addresses, call triage.geo.geocode_census
# (rate-limit + cache results in a table) to raise the geocoded share.

loc = spark.table(fq("harvey_locations"))
harvey_reports = (spark.table(fq("harvey_tweets")).join(loc, "tweet_id", "left")
    .withColumn("g", geocode(F.concat_ws(" ", "loc.location", "loc.street", "loc.neighborhood", "loc.zip"), "text"))
    .select(F.col("tweet_id").alias("report_id"), F.lit("harvey_replay").alias("source"), "text",
            F.lit("en").alias("lang"), F.col("g.lat").alias("lat"), F.col("g.lon").alias("lon"),
            "reported_at", F.lit(None).cast("string").alias("photo_path"),
            F.lit(None).cast("int").alias("photo_severity"), F.lit(None).cast("string").alias("reporter_contact"),
            F.lit("harvey_2017").alias("event"), F.col("label").alias("ground_truth_label")))
harvey_reports.write.mode("overwrite").saveAsTable(fq("harvey_reports"))
display(spark.sql(f"SELECT count(*) n, count(lat) geocoded FROM {fq('harvey_reports')}"))

# COMMAND ----------

# MAGIC %md ### Export relabel sample
# MAGIC HumAID's `requests_or_urgent_needs` mixes rescue and supplies. Relabel ~300 by hand
# MAGIC (`relabel` column: rescue_request / supplies_need / medical_emergency / other), upload as
# MAGIC `reference/humaid/relabels.csv`. Notebooks 05 and 08 use it automatically.

# COMMAND ----------

sample = (spark.table(fq("harvey_tweets")).filter("humaid_label = 'requests_or_urgent_needs'")
          .orderBy(F.rand(42)).limit(300).select("tweet_id", "text", F.lit("").alias("relabel")))
sample.toPandas().to_csv(f"{config.REFERENCE_DIR}/humaid/relabel_todo.csv", index=False)
print("Download from the reference volume:", f"{config.REFERENCE_DIR}/humaid/relabel_todo.csv")
