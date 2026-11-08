# Databricks notebook source
# Chaos job 3: bad data, not bad code. Some orders have 0 line items, so the average
# price per item divides by zero (ANSI mode raises). Expected diagnosis: data_quality;
# fix = nullif()/filter + an expectation, NOT "turn off ANSI".

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE workspace.doctor.order_item_stats AS
SELECT o.o_orderkey,
       o.o_totalprice / CAST(o.n_items AS INT) AS avg_item_price
FROM (SELECT *, CASE WHEN o_orderkey % 97 = 0 THEN 0 ELSE 3 END AS n_items
      FROM workspace.doctor.orders) o
""")
