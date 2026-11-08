# Databricks notebook source
# Chain step 3 (also DOWNSTREAM): customer lifetime value. Part of the blast radius.

# COMMAND ----------

spark.sql("""
CREATE OR REPLACE TABLE workspace.doctor.customer_ltv AS
SELECT o_custkey, SUM(o_totalprice) AS lifetime_value, COUNT(*) AS orders
FROM workspace.doctor.orders_clean
GROUP BY o_custkey
""")
