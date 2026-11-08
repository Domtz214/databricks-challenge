# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Reference data
# MAGIC Builds the lookup tables that turn a lat/lon into context:
# MAGIC
# MAGIC | Table | From | Used for |
# MAGIC |---|---|---|
# MAGIC | `ref_h3_area` | CDC SVI (tract) + Census tract centroids | county FIPS (alerts join) + vulnerability |
# MAGIC | `ref_gazetteer` | Census ZCTA/place centroids + hand list | geocoding tweets with no GPS |
# MAGIC | `ref_facilities` | shelters / hospitals / nursing homes CSVs | agent tools, forecast features |
# MAGIC | `ref_gauge_flood_stage` | NWS flood stages | "gauge X ft above flood stage" |
# MAGIC
# MAGIC **Manual downloads first** → see `docs/data_sources.md`; upload into the `reference` volume
# MAGIC (Catalog → workspace → triage → reference → Upload).

# COMMAND ----------

import os, sys
sys.path.append(os.path.abspath("../src"))
from pyspark.sql import functions as F
from triage import config
from triage.config import fq, H3_RES_AREA

REF = config.REFERENCE_DIR

# COMMAND ----------

# MAGIC %md ## 1. Tracts → H3 area cells with county + SVI

# COMMAND ----------

try:
    # CDC/ATSDR SVI 2022, Texas, census tract CSV. RPL_THEMES = overall percentile (0..1, -999 = missing).
    svi = (spark.read.option("header", True).csv(f"{REF}/svi/Texas.csv")
           .select(F.col("FIPS").alias("tract_geoid"),
                   F.when(F.col("RPL_THEMES").cast("double") < 0, None)
                    .otherwise(F.col("RPL_THEMES").cast("double")).alias("svi_percentile")))

    # Census Gazetteer tracts file (tab-separated). The last header has trailing spaces -> trim names.
    gaz = spark.read.option("header", True).option("sep", "\t").csv(f"{REF}/census/2023_Gaz_tracts_48.txt")
    gaz = gaz.toDF(*[c.strip() for c in gaz.columns])
    tracts = (gaz.select(F.col("GEOID").alias("tract_geoid"),
                         F.col("INTPTLAT").cast("double").alias("lat"),
                         F.col("INTPTLONG").cast("double").alias("lon"))
              .join(svi, "tract_geoid", "left")
              .withColumn("county_fips", F.substring("tract_geoid", 1, 5))
              .withColumn("tract_cell", F.expr(f"h3_longlatash3string(lon, lat, {H3_RES_AREA})")))

    # Fill every area cell near a tract centroid, keep the closest tract (grid distance).
    area = (tracts.withColumn("h3_area", F.explode(F.expr("h3_kring(tract_cell, 3)")))
            .withColumn("d", F.expr("h3_distance(h3_area, tract_cell)")))
    from pyspark.sql.window import Window
    w = Window.partitionBy("h3_area").orderBy("d")
    ref_h3_area = (area.withColumn("_rn", F.row_number().over(w)).filter("_rn = 1")
                   .select("h3_area", "county_fips", "tract_geoid", "svi_percentile"))
    ref_h3_area.write.mode("overwrite").saveAsTable(fq("ref_h3_area"))
    print(spark.table(fq("ref_h3_area")).count(), "area cells")
except Exception as e:
    print("SKIPPED tracts/SVI (upload svi/Texas.csv and census/2023_Gaz_tracts_48.txt):", str(e)[:200])

# COMMAND ----------

# MAGIC %md ## 2. Gazetteer (for geocoding free-text locations)

# COMMAND ----------

# Hand-curated places that tweets actually mention. Coordinates are approximate centers.
# TODO(you): extend with Houston neighborhoods/landmarks/major streets seen in Harvey tweets
# (notebook 02 prints the most common location strings from ai_extract to guide this).
seed_places = [
    ("Meyerland", 29.687, -95.459, "neighborhood"), ("Kingwood", 30.050, -95.185, "neighborhood"),
    ("Clear Lake", 29.560, -95.100, "neighborhood"), ("Bellaire", 29.706, -95.461, "city"),
    ("Pasadena", 29.691, -95.209, "city"), ("Katy", 29.786, -95.824, "city"),
    ("Dickinson", 29.461, -95.051, "city"), ("Friendswood", 29.529, -95.201, "city"),
    ("Port Arthur", 29.885, -93.940, "city"), ("Beaumont", 30.080, -94.102, "city"),
    ("Cypress", 29.969, -95.697, "city"), ("Spring", 30.080, -95.417, "city"),
    ("Braeswood", 29.690, -95.430, "street"), ("Telephone Rd", 29.680, -95.300, "street"),
    ("Kingwood Dr", 30.050, -95.200, "street"), ("Addicks", 29.792, -95.625, "landmark"),
    ("Barker Reservoir", 29.770, -95.650, "landmark"), ("Buffalo Bayou", 29.762, -95.380, "landmark"),
    ("College Station", 30.628, -96.334, "city"), ("Bryan", 30.674, -96.370, "city"),
]
places = spark.createDataFrame(seed_places, "name STRING, lat DOUBLE, lon DOUBLE, kind STRING")

# ZIP centroids (Census ZCTA gazetteer). Tweets often include a ZIP.
ZCTA_PATH = f"{REF}/census/2023_Gaz_zcta_national.txt"
try:
    if not os.path.exists(ZCTA_PATH):   # Spark reads lazily: fail here, not at the write below
        raise FileNotFoundError(ZCTA_PATH)
    zcta = spark.read.option("header", True).option("sep", "\t").csv(ZCTA_PATH)
    zcta = zcta.toDF(*[c.strip() for c in zcta.columns])
    zips = (zcta.filter(F.col("GEOID").between("75000", "79999") | F.col("GEOID").startswith("885"))
            .select(F.col("GEOID").alias("name"), F.col("INTPTLAT").cast("double").alias("lat"),
                    F.col("INTPTLONG").cast("double").alias("lon"), F.lit("zip").alias("kind")))
    places = places.unionByName(zips)
except Exception as e:
    print("ZIP centroids skipped (upload census/2023_Gaz_zcta_national.txt):", str(e)[:200])

places.write.mode("overwrite").saveAsTable(fq("ref_gazetteer"))
print(spark.table(fq("ref_gazetteer")).count(), "gazetteer entries")

# COMMAND ----------

# MAGIC %md ## 3. Facilities
# MAGIC One CSV per kind in `reference/facilities/`. Map each source's columns to
# MAGIC `facility_id, name, address, lat, lon, capacity` in the dict below.

# COMMAND ----------

# TODO(you): set these to the actual column names of the files you download.
FACILITY_SOURCES = {
    "shelter":      {"file": "shelters.csv",      "id": "SHELTER_ID", "name": "SHELTER_NAME", "address": "ADDRESS",
                     "lat": "LATITUDE", "lon": "LONGITUDE", "capacity": "EVACUATION_CAPACITY"},
    "hospital":     {"file": "hospitals.csv",     "id": "ID", "name": "NAME", "address": "ADDRESS",
                     "lat": "LATITUDE", "lon": "LONGITUDE", "capacity": "BEDS"},
    "nursing_home": {"file": "nursing_homes.csv", "id": "CMS Certification Number (CCN)", "name": "Provider Name",
                     "address": "Provider Address", "lat": "Latitude", "lon": "Longitude",
                     "capacity": "Number of Certified Beds"},
}

frames = []
for kind, m in FACILITY_SOURCES.items():
    path = f"{REF}/facilities/{m['file']}"
    if not os.path.exists(path):            # Spark reads lazily, so check up front
        print(f"skip {kind}: {path} not uploaded yet")
        continue
    df = spark.read.option("header", True).csv(path)
    missing = [m[k] for k in ("id", "name", "address", "lat", "lon", "capacity") if m[k] not in df.columns]
    if missing:
        print(f"skip {kind}: columns {missing} not in file. Available: {df.columns}")
        continue
    frames.append(df.select(
        F.concat_ws(":", F.lit(kind), F.col(f"`{m['id']}`")).alias("facility_id"),
        F.col(f"`{m['name']}`").alias("name"), F.lit(kind).alias("kind"),
        F.col(f"`{m['address']}`").alias("address"),
        F.col(f"`{m['lat']}`").cast("double").alias("lat"), F.col(f"`{m['lon']}`").cast("double").alias("lon"),
        F.col(f"`{m['capacity']}`").cast("int").alias("capacity"), F.lit(True).alias("is_open"),
    ))

if not frames:
    print("No facility files yet; ref_facilities stays empty (agent tools return no facilities).")
else:
    from functools import reduce
    fac = (reduce(lambda a, b: a.unionByName(b), frames)
           .filter("lat BETWEEN 25.8 AND 36.6 AND lon BETWEEN -106.7 AND -93.5")
           .withColumn("h3_area", F.expr(f"h3_longlatash3string(lon, lat, {H3_RES_AREA})")))
    fac.write.mode("overwrite").saveAsTable(fq("ref_facilities"))
    display(spark.table(fq("ref_facilities")).groupBy("kind").count())

# COMMAND ----------

# MAGIC %md ## 4. Gauge flood stages
# MAGIC NWS publishes flood stage per forecast point (NWPS). Simplest path for a hackathon:
# MAGIC a small CSV `reference/flood_stages.csv` with `site_id,flood_stage_ft` for the ~20–40 Houston-area
# MAGIC USGS gauges that matter (Buffalo, Brays, White Oak, Greens, Cypress Creek, San Jacinto…).

# COMMAND ----------

# TODO(you): build flood_stages.csv. Options:
#   a) NWPS API: https://api.water.noaa.gov/nwps/v1/gauges/{LID} -> flood categories + usgsId
#   b) Harris County Flood Warning System site pages list bank-full / flood stage
#   c) Fallback proxy: 95th percentile of the gauge's historical stage (label it as a proxy!)
try:
    fs = (spark.read.option("header", True).csv(f"{REF}/flood_stages.csv")
          .select(F.lpad("site_id", 8, "0").alias("site_id"),
                  F.col("flood_stage_ft").cast("double"), F.lit("manual").alias("source")))
    fs.write.mode("overwrite").saveAsTable(fq("ref_gauge_flood_stage"))
except Exception as e:
    print("flood_stages.csv not uploaded yet:", e)
