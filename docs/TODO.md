# What you need to complete

Ordered by the critical path. `grep -rn "TODO(you)" .` lists every code-level TODO.
⭐ = needed for the video's headline numbers. Never cut those.

## Phase 0: get it running (≈1 h)
- [ ] Workspace check: **Compute → Apps** and **Compute → Vector Search** exist? Serving page lists
      `databricks-gte-large-en`, a Llama/Claude chat model, and a vision-capable model? Update `src/triage/config.py`.
- [ ] Create a SQL warehouse (or use the starter one); note its ID.
- [ ] Git folder + `00_setup` + pipeline (README quick start). Confirm `gold_priority_queue` has rows.
- [ ] **Updating code:** run `notebooks/99_update_from_github` (pulls the branch, overwrites workspace files,
      refreshes the `code` volume). No Git folder or ZIP re-import needed.
- [ ] The pipeline imports `triage` from the `code` volume (pipeline compute can't read /Workspace .py files).
      **After any change in `src/`, rerun the "Sync shared code" cell in `00_setup`.**

## Phase 1: data (≈4 h)
- [ ] Download everything in `docs/data_sources.md`, upload to the `reference` volume.
- [ ] `01_reference_data`: fix `FACILITY_SOURCES` column names to match your files; build `flood_stages.csv`
      for ~20–40 Houston gauges.
- [ ] `02_prepare_harvey`: load HumAID, run `ai_extract`, check the geocoded share; extend the gazetteer
      with the top location strings it prints (big impact on map density).
- [ ] ⭐ Hand-relabel ~300 tweets (`relabel_todo.csv` → `relabels.csv`). This is your eval set (about 1 h, worth it).
- [ ] `04_poll_live_feeds`: run once manually; then run the Harvey gauge backfill (`RUN_BACKFILL = True`).

## Phase 2: models (≈5 h)
- [ ] `05_report_classifier`: three runs in MLflow (keywords / TF-IDF / ai_classify). Pick the pipeline mode:
      set `triage.classifier_mode` to `ai_functions` (best) or wire up `model` in `_classify`.
- [ ] ⭐ `06_rescue_forecast`: wire the gauge features (TODO block), train, check AUC / precision@20,
      set `THRESH`, produce `facility_lead_times`. If labels are too sparse, try res 6 or a 12 h horizon.
- [ ] ⭐ `08_backtest_harvey`: choose a capacity range where peak volume > capacity; record the numbers.
- [ ] Tune `WEIGHTS` / `TIER_CUTOFFS` in `src/triage/scoring.py` on the back-test (keep `pytest` green).

## Phase 3: agent (≈3 h)
- [ ] Download 3–6 guidance PDFs → `reference/guidance/`; run `07_guidance_index`
      (no Vector Search here: retrieval is SQL cosine similarity over `guidance_chunks`).
- [ ] Try the dispatcher prompt in **Playground** with your LLM endpoint; adjust `agent/prompts.py`.
- [ ] `09_agent_eval`: get valid-JSON rate ≈100%; screenshot an MLflow trace for the video.
- [ ] Photo assessment: confirm the vision endpoint accepts images (Playground → upload image).

## Phase 4: app + extras (≈3 h)
- [ ] `scripts/sync_app.sh`, deploy the app (bundle or Compute → Apps → Create → custom, source `app/`),
      add the SQL warehouse resource, run `scripts/grant_app_permissions.sql`.
- [ ] Test the report page on your phone (GPS permission, Spanish, photo).
- [ ] Genie space + SQL Alert: `docs/genie_and_alerts.md`. Put the Genie URL in `app/app.yaml`.
- [ ] Column mask on `reporter_contact` (bottom of `grant_app_permissions.sql`).
- [ ] For the replay: archived 2017 NWS alerts (Iowa Environmental Mesonet) → `in_flood_warning` in the
      back-test and the replay (nice-to-have; live mode already uses real alerts).

## Phase 5: ship (≈3 h)
- [ ] Set pipeline `continuous: true` while recording. Run `harvey_replay` job, record the console.
- [ ] Film 4–5 friends submitting reports (EN + ES, one photo) in a split screen.
- [ ] Record per `docs/video_script.md`; burn in captions.
- [ ] README: replace placeholders with your real numbers + a screenshot/GIF; Devpost write-up.

## Cut list if behind (in this order)
1. Photo assessment → 2. Gazetteer/Census geocoding improvements → 3. Genie → 4. agent tools beyond
`get_incident` + `search_guidance` → 5. mobile GPS (type the address instead).
