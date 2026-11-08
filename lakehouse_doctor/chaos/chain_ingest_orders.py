# Databricks notebook source
# Chain step 1 (UPSTREAM): cleans raw orders into orders_clean. In `drift_type` mode a "new
# vendor feed" formats prices as text ("1,234.50"); in `drift_cents` mode prices arrive in
# cents. Either way THIS JOB SUCCEEDS: the damage shows up downstream.

# COMMAND ----------

dbutils.widgets.dropdown("mode", "good", ["good", "drift_type", "drift_cents"])
mode = dbutils.widgets.get("mode")

from pyspark.sql import functions as F

clean = spark.read.table("workspace.doctor.orders").select("o_orderkey", "o_custkey", "o_orderdate", "o_totalprice")
if mode == "drift_type":
    clean = clean.withColumn("o_totalprice", F.format_number("o_totalprice", 2))
elif mode == "drift_cents":
    clean = clean.withColumn("o_totalprice", (F.col("o_totalprice") * 100).cast("bigint"))

clean.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable("workspace.doctor.orders_clean")
print("wrote orders_clean in mode", mode)
