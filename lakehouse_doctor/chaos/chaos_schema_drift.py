# Databricks notebook source
# Chaos job 2: upstream schema drift. A "new feed" sends o_totalprice as a formatted
# string ("$1,234.50"). Appending to a DOUBLE column fails. Expected diagnosis:
# schema_mismatch; fix = parse/cast before writing (and add an expectation upstream).

# COMMAND ----------

from pyspark.sql import functions as F

new_feed = (spark.read.table("workspace.doctor.orders").limit(100)
            .withColumn("o_totalprice", F.format_number("o_totalprice", 2)))  # now a STRING like "1,234.50"
new_feed.write.mode("append").saveAsTable("workspace.doctor.orders")
