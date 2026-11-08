"""Mobile-friendly report page: what a person in the flood (or a friend in your video) uses."""

import base64
import uuid
from datetime import datetime, timezone

import streamlit as st

from lib import data
from triage import taxonomy

st.title("📱 Report an emergency · Reportar una emergencia")
st.caption("If you can reach 911, call 911. · Si puede comunicarse al 911, llame al 911.")

lang = st.radio("Language · Idioma", ["English", "Español"], horizontal=True)
es = lang == "Español"

text = st.text_area(
    "¿Qué está pasando? ¿Cuántas personas? ¿Alguien necesita atención médica?" if es
    else "What is happening? How many people? Does anyone need medical care?",
    placeholder="Tip: tap the 🎤 on your phone keyboard to dictate." if not es
    else "Consejo: toque el 🎤 del teclado para dictar.",
    height=140,
)
# Voice: phone keyboards already do speech-to-text (English + Spanish), so no STT model is needed.
# TODO(you) stretch: st.audio_input + a transcription endpoint if you want in-app recording.

photo = st.camera_input("Photo (optional) · Foto (opcional)") or st.file_uploader(
    "…or upload a photo", type=["jpg", "jpeg", "png"])

# Location: browser GPS (asks permission), with a typed fallback.
lat = lon = None
try:
    from streamlit_js_eval import get_geolocation

    if st.checkbox("📍 Use my location · Usar mi ubicación", value=True):
        loc = get_geolocation()
        if loc and "coords" in loc:
            lat, lon = loc["coords"]["latitude"], loc["coords"]["longitude"]
            st.caption(f"GPS: {lat:.4f}, {lon:.4f}")
except Exception:
    pass
where = st.text_input("Address or neighborhood · Dirección o vecindario" + ("" if lat is None else " (optional)"))

if st.button("Send · Enviar", type="primary", disabled=not text.strip()):
    photo_path = photo_severity = None
    if photo is not None:
        raw = photo.getvalue()
        photo_path = data.upload_photo(raw)
        try:
            assessment = data.agent().assess_photo(base64.b64encode(raw).decode())
            photo_severity = int(assessment.get("severity", 0))
        except Exception as e:
            st.caption(f"(photo not assessed: {e})")

    full_text = f"{text.strip()} {where.strip()}".strip()
    report = {
        "report_id": str(uuid.uuid4()), "source": "app", "text": full_text, "lang": "es" if es else "en",
        "lat": lat, "lon": lon, "reported_at": datetime.now(timezone.utc).isoformat(),
        "photo_path": photo_path, "photo_severity": photo_severity,
        "reporter_contact": None, "event": "live", "ground_truth_label": None,
    }
    data.submit_report(report)
    data.trigger_pipeline()

    kw = taxonomy.classify_keywords(full_text)  # instant provisional triage
    tier = "P1" if kw.category in taxonomy.LIFE_THREATENING and kw.vulnerable else (
        "P2" if kw.category in taxonomy.LIFE_THREATENING else "P3")
    with st.spinner("…"):
        try:
            reply = data.agent().reply_to_reporter(full_text, "es" if es else "en", lat, lon, kw.category, tier)
        except Exception as e:
            reply = ("Recibimos su reporte." if es else "We received your report.") + f" ({e})"
    st.success(reply)
