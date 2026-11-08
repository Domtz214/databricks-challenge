# Databricks notebook source
# Chain step 2 (DOWNSTREAM): daily revenue from orders_clean. Its code never changes; it fails
# (or silently goes wrong) only because of what happened upstream.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE workspace.doctor.daily_revenue_v2 AS
SELECT o_orderdate, SUM(o_totalprice) AS revenue, COUNT(*) AS orders
FROM workspace.doctor.orders_clean
GROUP BY o_orderdate
""")
