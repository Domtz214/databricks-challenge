# Lakeflow Declarative Pipeline (formerly Delta Live Tables) for Triage.
#
#   landing/reports  (JSON lines: replay, live app, synthetic) ─┐
#   landing/alerts   (NWS, polled by notebook 04)              ├─ Auto Loader
#   landing/gauges   (USGS, polled by notebook 04)             ─┘
#        │
#   BRONZE  bronze_reports, bronze_alerts, bronze_gauges        (raw, append-only)
#   SILVER  silver_reports        (dedupe, classify, extract, geocode, H3)
#           silver_alerts_active  (one row per active alert x county)
#           silver_gauges_latest  (latest stage + 3h rise + flood stage)
#   GOLD    gold_incidents        (reports clustered by H3 cell + category)
#           gold_priority_queue   (incident + hazard + vulnerability + forecast -> urgency)
#
# Configuration keys (set in databricks.yml):
#   triage.catalog, triage.schema, triage.src_path,
#   triage.classifier_mode = keywords | ai_functions | model
#   triage.now_mode        = wall | replay   (replay: "now" = latest report time)

import sys

import dlt
from pyspark.sql import functions as F
from pyspark.sql import types as T
from pyspark.sql.window import Window

CATALOG = spark.conf.get("triage.catalog", "workspace")  # noqa: F821  (spark is injected)
SCHEMA = spark.conf.get("triage.schema", "triage")  # noqa: F821
CLASSIFIER_MODE = spark.conf.get("triage.classifier_mode", "keywords")  # noqa: F821
NOW_MODE = spark.conf.get("triage.now_mode", "replay")  # noqa: F821
SRC_PATH = spark.conf.get("triage.src_path")  # noqa: F821
sys.path.append(SRC_PATH)


def _ensure_src():
    """UDFs execute on workers, which need the same sys.path entry.
    TODO(you): if `import triage` fails on workers, build src/ as a wheel and add
    it under the pipeline's environment dependencies instead."""
    import sys as _sys

    if SRC_PATH not in _sys.path:
        _sys.path.append(SRC_PATH)

from triage import scoring, taxonomy  # noqa: E402
from triage.config import H3_RES_AREA, H3_RES_INCIDENT  # noqa: E402

LANDING = f"/Volumes/{CATALOG}/{SCHEMA}/landing"


def fq(name: str) -> str:
    return f"{CATALOG}.{SCHEMA}.{name}"


REPORT_SCHEMA = (
    "report_id STRING, source STRING, text STRING, lang STRING, lat DOUBLE, lon DOUBLE, "
    "reported_at TIMESTAMP, photo_path STRING, photo_severity INT, reporter_contact STRING, "
    "event STRING, ground_truth_label STRING"
)
ALERT_SCHEMA = (
    "alert_id STRING, event STRING, severity STRING, urgency STRING, certainty STRING, "
    "headline STRING, area_desc STRING, county_fips ARRAY<STRING>, ugc_codes ARRAY<STRING>, "
    "sent TIMESTAMP, effective TIMESTAMP, expires TIMESTAMP, geometry_json STRING, fetched_at TIMESTAMP"
)
GAUGE_SCHEMA = (
    "site_id STRING, site_name STRING, lat DOUBLE, lon DOUBLE, parameter_code STRING, "
    "value DOUBLE, observed_at TIMESTAMP"
)


def autoload(path: str, schema: str):
    return (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", "json")
        .schema(schema)
        .load(path)
        .withColumn("_source_file", F.col("_metadata.file_path"))
        .withColumn("_ingested_at", F.current_timestamp())
    )


# ─────────────────────────────── BRONZE ───────────────────────────────

@dlt.table(comment="Raw incoming reports (replay, app, synthetic). Append-only.")
def bronze_reports():
    return autoload(f"{LANDING}/reports", REPORT_SCHEMA)


@dlt.table(comment="Raw NWS alerts, one row per alert per poll.")
def bronze_alerts():
    return autoload(f"{LANDING}/alerts", ALERT_SCHEMA)


@dlt.table(comment="Raw USGS gauge readings.")
def bronze_gauges():
    return autoload(f"{LANDING}/gauges", GAUGE_SCHEMA)


# ─────────────────────────────── SILVER ───────────────────────────────

_kw_schema = T.StructType([
    T.StructField("category", T.StringType()),
    T.StructField("p_life_threat", T.DoubleType()),
    T.StructField("vulnerable", T.BooleanType()),
    T.StructField("people_count", T.IntegerType()),
])


@F.pandas_udf(_kw_schema)
def keyword_classify(text):
    import pandas as pd

    _ensure_src()
    from triage import taxonomy

    rows = [taxonomy.classify_keywords(t or "") for t in text]
    return pd.DataFrame({
        "category": [r.category for r in rows],
        "p_life_threat": [r.p_life_threat for r in rows],
        "vulnerable": [r.vulnerable for r in rows],
        "people_count": [r.people_count for r in rows],
    })


def _classify(df):
    """Adds: category, p_life_threat, vulnerable, people_count, location_text, text_en."""
    kw = keyword_classify(F.col("text"))
    df = df.withColumn("_kw", kw)

    if CLASSIFIER_MODE == "ai_functions":
        # Databricks AI Functions run on every row inside the pipeline.
        # TODO(you): verify AI Functions are allowed in your pipeline/workspace;
        # if not, run the same SQL in notebook 02 and keep this pipeline on keywords.
        labels = ", ".join(f"'{c}'" for c in taxonomy.CATEGORIES)
        df = (
            df.withColumn("text_en", F.when(F.col("lang") == "es", F.expr("ai_translate(text, 'en')"))
                          .otherwise(F.col("text")))
            .withColumn("category", F.expr(f"ai_classify(text_en, array({labels}))"))
            .withColumn("_x", F.expr(
                "ai_extract(text_en, array('location', 'people', 'medical_needs'))"))
            .withColumn("location_text", F.col("_x.location"))
            .withColumn("people_count", F.coalesce(
                F.expr("try_cast(regexp_extract(_x.people, '(\\\\d+)', 1) AS INT)"),
                F.col("_kw.people_count")))
            # TODO(you): replace with a calibrated probability (ai_query with a
            # responseFormat, or the MLflow model). Fixed values are a placeholder.
            .withColumn("p_life_threat", F.when(F.col("category").isin(*taxonomy.LIFE_THREATENING), 0.9)
                        .otherwise(0.05))
            .withColumn("vulnerable", F.col("_kw.vulnerable") | F.col("_x.medical_needs").isNotNull())
            .drop("_x")
        )
    elif CLASSIFIER_MODE == "model":
        # TODO(you): after notebook 05 registers the model, score with spark_udf:
        #   import mlflow; clf = mlflow.pyfunc.spark_udf(spark, f"models:/{fq('urgency_classifier')}@champion",
        #                                                result_type="string")
        #   df = df.withColumn("category", clf("text"))
        raise NotImplementedError("classifier_mode=model: wire up notebook 05's model here")
    else:  # keywords (default; free, deterministic, bilingual)
        df = (
            df.withColumn("text_en", F.col("text"))
            .withColumn("category", F.col("_kw.category"))
            .withColumn("p_life_threat", F.col("_kw.p_life_threat"))
            .withColumn("vulnerable", F.col("_kw.vulnerable"))
            .withColumn("people_count", F.col("_kw.people_count"))
            .withColumn("location_text", F.lit(None).cast("string"))
        )
    return df.drop("_kw")


@dlt.table(comment="Cleaned, deduplicated, classified and geolocated reports.")
@dlt.expect_or_drop("has_text", "text IS NOT NULL AND length(trim(text)) > 3")
@dlt.expect_or_drop("has_time", "reported_at IS NOT NULL")
@dlt.expect("has_location", "lat IS NOT NULL AND lon IS NOT NULL")
@dlt.expect("in_texas_bbox", "lat IS NULL OR (lat BETWEEN 25.8 AND 36.6 AND lon BETWEEN -106.7 AND -93.5)")
def silver_reports():
    df = (
        dlt.read_stream("bronze_reports")
        .withColumn("text_hash", F.sha2(F.lower(F.regexp_replace("text", r"\s+", " ")), 256))
        .withWatermark("reported_at", "6 hours")
        .dropDuplicatesWithinWatermark(["text_hash"])   # retweets / double submits
        .withColumn("lang", F.coalesce("lang", F.lit("en")))
    )
    df = _classify(df)

    # Geocode reports without GPS using the gazetteer (neighborhoods, landmarks, ZIPs).
    # TODO(you): build ref_gazetteer in notebook 01. Until then rows keep NULL lat/lon
    # (the has_location expectation shows how many in the pipeline UI).
    gaz = spark.read.table(fq("ref_gazetteer")).select("name", "lat", "lon").collect()  # noqa: F821
    places = [(r["name"], r["lat"], r["lon"]) for r in gaz]

    @F.pandas_udf("struct<glat:double, glon:double>")
    def gazetteer_geocode(text, loc):
        import pandas as pd

        _ensure_src()
        from triage.geo import Gazetteer, Place

        g = Gazetteer([Place(n, la, lo) for n, la, lo in places])
        out = []
        for t, l in zip(text, loc):
            p = g.match(f"{l or ''} {t or ''}")
            out.append((p.lat, p.lon) if p else (None, None))
        return pd.DataFrame(out, columns=["glat", "glon"])

    df = (
        df.withColumn("_g", gazetteer_geocode("text", "location_text"))
        .withColumn("geo_method", F.when(F.col("lat").isNotNull(), "gps")
                    .when(F.col("_g.glat").isNotNull(), "gazetteer").otherwise(None))
        .withColumn("lat", F.coalesce("lat", "_g.glat"))
        .withColumn("lon", F.coalesce("lon", "_g.glon"))
        .drop("_g")
        .withColumn("h3_cell", F.expr(f"h3_longlatash3string(lon, lat, {H3_RES_INCIDENT})"))
        .withColumn("h3_area", F.expr(f"h3_longlatash3string(lon, lat, {H3_RES_AREA})"))
    )
    return df


@dlt.table(comment="Active NWS alerts exploded to county FIPS (latest version of each alert).")
def silver_alerts_active():
    w = Window.partitionBy("alert_id").orderBy(F.col("fetched_at").desc())
    now = F.current_timestamp()
    return (
        dlt.read("bronze_alerts")
        .withColumn("_rn", F.row_number().over(w)).filter("_rn = 1").drop("_rn")
        .filter(F.col("expires").isNull() | (F.col("expires") > now))
        .withColumn("county_fips", F.explode_outer("county_fips"))
        .withColumn("is_flood_related", F.col("event").rlike("(?i)flood|hurricane|tropical storm|storm surge"))
        .select("alert_id", "event", "severity", "urgency", "headline", "county_fips",
                "effective", "expires", "is_flood_related")
    )
    # TODO(you) for the Harvey back-test: alerts are wall-clock "active". For the
    # replay, load archived 2017 alerts (Iowa Environmental Mesonet archive) into
    # a separate table and filter by the replay clock instead.


@dlt.table(comment="Latest stage per gauge, 3h rise, and NWS flood stage (ft above flood).")
def silver_gauges_latest():
    g = dlt.read("bronze_gauges").filter("parameter_code = '00065'")
    w_latest = Window.partitionBy("site_id").orderBy(F.col("observed_at").desc())
    latest = g.withColumn("_rn", F.row_number().over(w_latest)).filter("_rn = 1").drop("_rn")
    three_h_ago = (
        g.alias("g").join(latest.select("site_id", F.col("observed_at").alias("t_latest")), "site_id")
        .filter(F.col("observed_at") <= F.col("t_latest") - F.expr("INTERVAL 3 HOURS"))
        .groupBy("site_id").agg(F.max_by("value", "observed_at").alias("stage_3h_ago"))
    )
    stages = spark.read.table(fq("ref_gauge_flood_stage"))  # noqa: F821  (site_id, flood_stage_ft)
    return (
        latest.join(three_h_ago, "site_id", "left")
        .join(stages, "site_id", "left")
        .select(
            "site_id", "site_name", "lat", "lon", "observed_at",
            F.col("value").alias("stage_ft"),
            (F.col("value") - F.col("stage_3h_ago")).alias("rise_ft_3h"),
            "flood_stage_ft",
            (F.col("value") - F.col("flood_stage_ft")).alias("ft_above_flood"),
            F.expr(f"h3_longlatash3string(lon, lat, {H3_RES_AREA})").alias("h3_area"),
        )
    )


# ──────────────────────────────── GOLD ────────────────────────────────

@dlt.table(comment="Reports clustered into incidents: same H3 res-9 cell + category.")
def gold_incidents():
    # TODO(you) stretch: sessionize by time gap (e.g. new incident if >6h since last
    # report in the cell) so a cell can have multiple incidents over a multi-day storm.
    r = dlt.read("silver_reports").filter("category <> 'not_relevant' AND h3_cell IS NOT NULL")
    area = spark.read.table(fq("ref_h3_area"))  # noqa: F821  (h3_area, county_fips, svi_percentile)
    return (
        r.groupBy("h3_cell", "category")
        .agg(
            F.first("h3_area").alias("h3_area"),
            F.avg("lat").alias("lat"), F.avg("lon").alias("lon"),
            F.min("reported_at").alias("first_reported_at"),
            F.max("reported_at").alias("last_reported_at"),
            F.count("*").alias("n_reports"),
            F.max("people_count").alias("people_count"),
            F.max(F.col("vulnerable").cast("int")).cast("boolean").alias("vulnerable"),
            F.max("p_life_threat").alias("p_life_threat"),
            F.max("photo_severity").alias("photo_severity"),
            F.slice(F.collect_list("text"), 1, 5).alias("sample_texts"),
            F.collect_list("report_id").alias("report_ids"),
            F.collect_set("lang").alias("langs"),
        )
        .withColumn("incident_id", F.sha2(F.concat_ws("|", "h3_cell", "category"), 256).substr(1, 16))
        .join(area, "h3_area", "left")
    )


_urgency_schema = "struct<urgency:double, tier:string, breakdown_json:string>"


@F.pandas_udf(_urgency_schema)
def urgency_udf(p_life, warn, ft_above, vuln, svi, minutes, n_rep, people, fc):
    import json

    import pandas as pd

    _ensure_src()
    from triage import scoring

    def opt(x):
        return None if pd.isna(x) else x

    out = []
    for row in zip(p_life, warn, ft_above, vuln, svi, minutes, n_rep, people, fc):
        s = scoring.IncidentSignals(
            p_life_threat=float(row[0] or 0), in_flood_warning=bool(row[1]),
            gauge_ft_above_flood=opt(row[2]), vulnerable=bool(row[3]),
            svi_percentile=opt(row[4]), minutes_waiting=float(row[5] or 0),
            n_reports=int(row[6] or 1), people_count=None if pd.isna(row[7]) else int(row[7]),
            forecast_risk=opt(row[8]),
        )
        res = scoring.urgency(s)
        out.append((res.score, res.tier, json.dumps(res.breakdown, default=str)))
    return pd.DataFrame(out, columns=["urgency", "tier", "breakdown_json"])


@dlt.table(comment="Ranked dispatch queue. The App and the agent read this.")
def gold_priority_queue():
    inc = dlt.read("gold_incidents")

    alerts = (
        dlt.read("silver_alerts_active").filter("is_flood_related")
        .groupBy("county_fips").agg(F.collect_set("event").alias("alert_events"))
        .withColumn("in_flood_warning", F.lit(True))
    )

    # Nearest gauge: candidate gauges within 2 rings of the incident's area cell.
    gauges = dlt.read("silver_gauges_latest").filter("h3_area IS NOT NULL")
    cand = (
        inc.select("incident_id", "h3_area", "lat", "lon")
        .withColumn("ring", F.explode(F.expr("h3_kring(h3_area, 2)")))
        .join(gauges.select("site_id", F.col("h3_area").alias("ring"), F.col("lat").alias("glat"),
                            F.col("lon").alias("glon"), "ft_above_flood"), "ring")
        .withColumn("d", F.expr("pow(lat - glat, 2) + pow(lon - glon, 2)"))
    )
    nearest = (
        cand.withColumn("_rn", F.row_number().over(Window.partitionBy("incident_id").orderBy("d")))
        .filter("_rn = 1").select("incident_id", F.col("site_id").alias("gauge_site_id"),
                                  F.col("ft_above_flood").alias("gauge_ft_above_flood"))
    )

    # Latest forecast per area (written by notebook 06; table may be empty at first).
    fc = (
        spark.read.table(fq("forecast_rescue_need"))  # noqa: F821
        .withColumn("_rn", F.row_number().over(Window.partitionBy("h3_area").orderBy(F.col("forecast_for").desc())))
        .filter("_rn = 1").select("h3_area", F.col("risk").alias("forecast_risk"))
    )

    decisions = spark.read.table(fq("dispatch_decisions")).select("incident_id", "decision")  # noqa: F821

    if NOW_MODE == "replay":
        now_col = F.max("last_reported_at").over(Window.partitionBy())
    else:
        now_col = F.current_timestamp()

    q = (
        inc.join(alerts, "county_fips", "left")
        .join(nearest, "incident_id", "left")
        .join(fc, "h3_area", "left")
        .join(decisions, "incident_id", "left")
        .withColumn("in_flood_warning", F.coalesce("in_flood_warning", F.lit(False)))
        .withColumn("status", F.when(F.col("decision").isNull(), "open").otherwise(F.col("decision")))
        .withColumn("_now", now_col)
        .withColumn("minutes_waiting",
                    (F.col("_now").cast("long") - F.col("first_reported_at").cast("long")) / 60.0)
        .withColumn("_u", urgency_udf(
            "p_life_threat", "in_flood_warning", "gauge_ft_above_flood", "vulnerable",
            "svi_percentile", "minutes_waiting", "n_reports", "people_count", "forecast_risk"))
        .select("*", "_u.*").drop("_u", "_now", "decision")
    )
    return q
