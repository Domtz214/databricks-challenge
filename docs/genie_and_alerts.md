# Genie space, SQL Alert, Dashboard

## Genie space (SQL → Genie Agents → New)
**Tables:** `gold_priority_queue`, `gold_incidents`, `silver_reports`, `silver_alerts_active`,
`silver_gauges_latest`, `ref_facilities`, `forecast_rescue_need`, `dispatch_decisions`.

**Instructions (paste):**
```
This is an emergency-operations dataset for flood response in Texas.
- "Open" incidents: gold_priority_queue.status = 'open'. Priority tiers P1 (most urgent) .. P4.
- Life-threatening categories: rescue_request, medical_emergency.
- county_fips 48201 = Harris County, 48041 = Brazos County, 48157 = Fort Bend, 48167 = Galveston.
- forecast_rescue_need.risk is the probability of a rescue request in that H3 cell in the next horizon_h hours.
- Join facilities to forecasts on h3_area. Prefer counts and maps over long tables.
```
**Sample questions (add as examples, use in the video):**
- How many open P1 incidents are there by county?
- Which nursing homes are in cells with forecast risk above 0.5?
- What share of reports were in Spanish, and how does their median urgency compare to English?
- Which gauges are above flood stage and rising?

## SQL Alert (SQL → Alerts → New): the "facility at risk" moment
Query (save as `facilities_at_risk`):
```sql
SELECT count(*) AS facilities_at_risk
FROM workspace.triage.ref_facilities f
JOIN (SELECT h3_area, max_by(risk, forecast_for) AS risk
      FROM workspace.triage.forecast_rescue_need GROUP BY 1) r USING (h3_area)
WHERE f.kind IN ('nursing_home', 'hospital', 'dialysis') AND r.risk >= 0.5;
```
Condition: `facilities_at_risk > 0`. Schedule every minute while recording. Notify: your email (shows on your
phone in the video).

Second alert idea: `SELECT count(*) FROM workspace.triage.gold_priority_queue WHERE tier = 'P1' AND status = 'open' AND minutes_waiting > 30`.

## AI/BI Dashboard (optional)
Counters (open P1, people reported, facilities at risk), a map of `gold_priority_queue` (lat/lon colored by tier),
line chart of reports per hour by category, bar chart of the back-test.
