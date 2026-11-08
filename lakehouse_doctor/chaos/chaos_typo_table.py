# Databricks notebook source
# Chaos job 1: a typo in a table name (a classic). Expected diagnosis: missing_table,
# fix `ordres` -> `orders` (table_info shows `orders` exists in the same schema).

# COMMAND ----------

df = spark.read.table("workspace.doctor.ordres")
daily = df.groupBy("o_orderdate").sum("o_totalprice")
daily.write.mode("overwrite").saveAsTable("workspace.doctor.daily_revenue")
