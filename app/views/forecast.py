import pydeck as pdk
import streamlit as st

from lib import data
from triage.config import DEFAULT_CENTER, fq

st.title("📈 Rescue-need forecast (next 6 h)")
st.caption("Where rescue requests are likely to come from *before* anyone asks. Model: notebook 06.")

fc = data.latest_forecast()
if fc.empty:
    st.info("No forecast yet. Run notebook 06.")
    st.stop()

fc = fc.assign(color=fc.risk.apply(lambda r: [220, 38, 38, int(40 + 200 * r)]))
layer = pdk.Layer("H3HexagonLayer", fc, get_hexagon="h3_area", get_fill_color="color", pickable=True)
fac = data.facilities(("nursing_home", "hospital"))
fac_layer = pdk.Layer("ScatterplotLayer", fac, get_position="[lon, lat]", get_radius=300,
                      get_fill_color=[37, 99, 235, 220], pickable=True)
st.pydeck_chart(pdk.Deck(layers=[layer, fac_layer], map_style=None,
                         initial_view_state=pdk.ViewState(latitude=DEFAULT_CENTER[0], longitude=DEFAULT_CENTER[1], zoom=9),
                         tooltip={"text": "risk {risk}\n{name}"}))

st.subheader("Vulnerable facilities in high-risk cells")
at_risk = data.q(f"""
SELECT f.name, f.kind, f.capacity, round(r.risk, 2) AS risk
FROM {fq('ref_facilities')} f
JOIN (SELECT h3_area, max_by(risk, forecast_for) risk FROM {fq('forecast_rescue_need')} GROUP BY 1) r USING (h3_area)
WHERE f.kind IN ('nursing_home', 'hospital', 'dialysis') AND r.risk >= 0.5
ORDER BY risk DESC LIMIT 25""")
st.dataframe(at_risk, hide_index=True, use_container_width=True)
# TODO(you): the SQL Alert (docs/genie_and_alerts.md) on this same query is what fires in the video.
