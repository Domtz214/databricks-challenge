from datetime import datetime, timedelta, timezone

from triage import backtest, feeds, geo, replay, scoring, taxonomy


# ---- taxonomy -----------------------------------------------------------

def test_keywords_english_rescue_vulnerable():
    r = taxonomy.classify_keywords("Trapped in the attic with my grandma, water is rising, 3 people")
    assert r.category == "rescue_request"
    assert r.vulnerable
    assert r.people_count == 3


def test_keywords_spanish_medical_with_accents():
    r = taxonomy.classify_keywords("Mi abuela necesita diálisis, somos cuatro personas")
    assert r.category == "medical_emergency"
    assert r.vulnerable
    assert r.people_count == 4


def test_keywords_irrelevant():
    assert taxonomy.classify_keywords("Praying for Texas tonight").category == "not_relevant"


def test_humaid_mapping_covers_taxonomy():
    assert set(taxonomy.HUMAID_TO_TRIAGE.values()) <= set(taxonomy.CATEGORIES)


# ---- scoring ------------------------------------------------------------

def test_urgency_monotonic_in_hazard():
    base = scoring.IncidentSignals(p_life_threat=0.8)
    worse = scoring.IncidentSignals(p_life_threat=0.8, in_flood_warning=True, gauge_ft_above_flood=4)
    assert scoring.urgency(worse).score > scoring.urgency(base).score


def test_hard_rule_forces_p1():
    s = scoring.IncidentSignals(p_life_threat=0.5, in_flood_warning=True, vulnerable=True)
    assert scoring.urgency(s).tier == "P1"
    assert scoring.urgency(s).breakdown["hard_rule_p1"]


def test_irrelevant_is_low_tier():
    assert scoring.urgency(scoring.IncidentSignals(p_life_threat=0.02)).tier == "P4"


# ---- backtest -----------------------------------------------------------

def _reports():
    t0 = datetime(2017, 8, 27, tzinfo=timezone.utc)
    rs = [backtest.QueuedReport(f"o{i}", t0 + timedelta(minutes=i), 0.1, False) for i in range(20)]
    rs.append(backtest.QueuedReport("life", t0 + timedelta(minutes=5), 2.0, True))
    return rs


def test_priority_beats_fifo_for_life_threatening():
    rs = _reports()
    fifo = backtest.simulate(rs, capacity_per_hour=6, policy="fifo")
    prio = backtest.simulate(rs, capacity_per_hour=6, policy="priority")
    assert prio["life"] < fifo["life"]
    assert len(fifo) == len(prio) == len(rs)


def test_compare_policies_positive_savings():
    res = backtest.compare_policies(_reports(), capacity_per_hour=6)
    assert res["life_median_minutes_saved"] > 0


def test_lead_time():
    t = datetime(2017, 8, 27, 12)
    assert backtest.lead_time_hours(t, t + timedelta(hours=3)) == 3.0
    assert backtest.lead_time_hours(None, t) is None


# ---- feeds --------------------------------------------------------------

def test_parse_nws():
    payload = {"features": [{"id": "x", "geometry": None, "properties": {
        "id": "urn:1", "event": "Flash Flood Warning", "severity": "Severe",
        "geocode": {"SAME": ["048201", "048157"], "UGC": ["TXC201"]},
        "expires": "2026-11-08T12:00:00-06:00"}}]}
    rows = feeds.parse_nws_alerts(payload)
    assert rows[0]["county_fips"] == ["48201", "48157"]
    assert rows[0]["event"] == "Flash Flood Warning"


def test_parse_usgs_skips_nodata():
    payload = {"value": {"timeSeries": [{
        "sourceInfo": {"siteName": "Buffalo Bayou", "siteCode": [{"value": "08074000"}],
                       "geoLocation": {"geogLocation": {"latitude": 29.76, "longitude": -95.40}}},
        "variable": {"noDataValue": -999999.0, "variableCode": [{"value": "00065"}]},
        "values": [{"value": [{"value": "31.2", "dateTime": "2017-08-27T10:00:00.000-05:00"},
                              {"value": "-999999", "dateTime": "2017-08-27T10:15:00.000-05:00"}]}]}]}}
    rows = feeds.parse_usgs_iv(payload)
    assert len(rows) == 1 and rows[0]["value"] == 31.2 and rows[0]["site_id"] == "08074000"


# ---- geo / replay -------------------------------------------------------

def test_gazetteer_longest_match():
    g = geo.Gazetteer([geo.Place("Clear Lake", 29.56, -95.10), geo.Place("Lake", 0, 0)])
    assert g.match("stuck near clear lake shores").name == "Clear Lake"
    assert g.match("nothing here") is None


def test_haversine_houston_to_college_station():
    assert 130 < geo.haversine_km(29.76, -95.37, 30.63, -96.33) < 138  # ~134 km straight line


def test_tweet_id_timestamp_harvey():
    # A tweet id from late Aug 2017 decodes to Aug 2017.
    dt = replay.tweet_id_to_datetime(901480000000000000)
    assert (dt.year, dt.month) == (2017, 8)


def test_synthetic_reports_schema():
    rows = replay.synthetic_reports(10, center=(29.76, -95.37), seed=1)
    assert len(rows) == 10
    assert {"report_id", "text", "lang", "lat", "lon", "reported_at"} <= rows[0].keys()
    assert {r["lang"] for r in replay.synthetic_reports(200, (29.76, -95.37), seed=2)} == {"en", "es"}
