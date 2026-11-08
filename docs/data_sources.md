# Data sources

Upload manual downloads into the UC volume `workspace.triage.reference` (Catalog → workspace → triage → Volumes →
reference → Upload) using the folder/file names below. All sources are public. Check each site's terms.

| Data | Where | Save as | Used by |
|---|---|---|---|
| **HumAID** (Harvey event: train/dev/test TSV: `tweet_id`, `tweet_text`, `class_label`) | crisisnlp.qcri.org → HumAID dataset | `humaid/hurricane_harvey_2017_{train,dev,test}.tsv` | 02, 05, 08 |
| **CDC/ATSDR SVI 2022**, Texas, census tracts, CSV | atsdr.cdc.gov → Place and Health → SVI → Data download | `svi/Texas.csv` | 01 |
| **Census Gazetteer**: tracts (Texas) + ZCTA (national) | census.gov → Geographies → Gazetteer Files | `census/2023_Gaz_tracts_48.txt`, `census/2023_Gaz_zcta_national.txt` | 01 |
| **Shelters** (FEMA National Shelter System open data, or Red Cross / county lists) | FEMA GIS open data / ArcGIS Hub | `facilities/shelters.csv` | 01, agent |
| **Hospitals** (HIFLD mirror, CMS Hospital General Info + geocode, or OSM) | various | `facilities/hospitals.csv` | 01, agent, 06 |
| **Nursing homes**: CMS "Nursing Home Provider Information" (has lat/long) | data.cms.gov → Provider Data | `facilities/nursing_homes.csv` | 01, 06 |
| **Flood stages** for Houston-area gauges (`site_id,flood_stage_ft`) | NWS NWPS (api.water.noaa.gov/nwps/v1/gauges/{LID}) / Harris County FWS | `flood_stages.csv` | 01, pipeline, 06 |
| **Guidance PDFs**: Red Cross flood safety, FEMA flood fact sheets, NWS "Turn Around Don't Drown", ESF-9 search & rescue overview | redcross.org, ready.gov, weather.gov, fema.gov | `guidance/*.pdf` | 07, agent |

## Live APIs (no key)
- **NWS alerts:** `https://api.weather.gov/alerts/active?area=TX` (send a User-Agent; set in `config.py`)
- **USGS instantaneous values:** `https://waterservices.usgs.gov/nwis/iv/?stateCd=tx&parameterCd=00065&format=json`
  (USGS is migrating to `api.waterdata.usgs.gov`; switch if the legacy service is retired)
- **Census geocoder:** `https://geocoding.geo.census.gov/geocoder/locations/onelineaddress`
- **USGS elevation (EPQS):** `https://epqs.nationalmap.gov/v1/json?x=LON&y=LAT&units=Feet`

## Nice-to-have
- **Archived NWS alerts for Aug 2017**: Iowa Environmental Mesonet (IEM) VTEC/warnings archive → `in_flood_warning` in the back-test.
- **OpenFEMA** Individual Assistance registrations for DR-4332 (Harvey) by ZIP: damage ground truth / static prior.
- **CrisisMMD** (Harvey tweets with images): demo images for photo assessment.

## Databricks Marketplace
Search for free weather / geospatial / census datasets. Anything you can mount there saves an upload step.
