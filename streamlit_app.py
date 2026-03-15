import streamlit as st
import requests
import gzip, json, base64
import numpy as np
import pandas as pd
import plotly.express as px
import time

st.set_page_config(page_title="NCEP Climate Scatter", page_icon="🌡️", layout="wide")
st.title("🌡️ NCEP Climate Dashboard")
st.caption("Pin a location on the globe to see its full temperature time series.")

# ── Load ncep_data.json.gz once and cache ─────────────────────────────────────
@st.cache_data
def load_ncep():
    with gzip.open("ncep_data.json.gz", "rb") as f:
        return json.loads(f.read())

NC    = load_ncep()
lats  = np.array(NC["lats"])
lons  = np.array(NC["lons"])
times = NC["times"]          # list of "YYYY-MM" strings
tmin  = NC["tmin"]
tmax  = NC["tmax"]
nlat  = NC["nlat"]
nlon  = NC["nlon"]
SPAN  = tmax - tmin
GRID  = nlat * nlon
raw   = np.frombuffer(base64.b64decode(NC["data_b64"]), dtype=np.uint8)

# ── Nearest grid index ────────────────────────────────────────────────────────
def nearest_idx(arr, v):
    return int(np.argmin(np.abs(arr - v)))

# ── Decode full time series for a lat/lon ─────────────────────────────────────
def get_time_series(lat, lon):
    lon_norm = lon % 360          # NCEP lons are 0–360
    li = nearest_idx(lats, lat)
    lj = nearest_idx(lons, lon_norm)
    indices = np.arange(len(times)) * GRID + li * nlon + lj
    temps   = tmin + (raw[indices].astype(float) / 255) * SPAN
    return temps

# ── Build monthly + yearly dataframes ─────────────────────────────────────────
def build_df(lat, lon):
    temps = get_time_series(lat, lon)
    years = [int(t[:4]) for t in times]
    monthly_df = pd.DataFrame({"time": times, "year": years, "temperature": temps})
    yearly_df  = monthly_df.groupby("year")["temperature"].mean().reset_index()
    yearly_df.columns = ["Year", "AvgTemperature"]
    return monthly_df, yearly_df

# ── Poll Flask for pinned lat/lon ─────────────────────────────────────────────
def get_pinned():
    try:
        resp = requests.get("http://127.0.0.1:5000/get_location", timeout=2)
        data = resp.json()
        if data.get("lat") is not None and data.get("lon") is not None:
            return float(data["lat"]), float(data["lon"])
    except:
        pass
    return None, None

# ── UI Layout ─────────────────────────────────────────────────────────────────
col1, col2 = st.columns([3, 1])

with col2:
    st.markdown("### 📍 Pinned Location")
    auto_refresh = st.toggle("Auto-refresh", value=True)
    refresh_rate = st.slider("Refresh every (seconds)", 2, 10, 3)
    if st.button("🔄 Refresh Now", use_container_width=True):
        st.rerun()
    st.divider()
    info_box  = st.empty()
    stats_box = st.empty()

with col1:
    chart_placeholder   = st.empty()
    monthly_placeholder = st.empty()

# ── Main logic ────────────────────────────────────────────────────────────────
lat, lon = get_pinned()

if lat is None:
    with col2:
        info_box.info("No location pinned yet.\nGo to the globe and click somewhere.")
    with col1:
        chart_placeholder.markdown(
            "<div style='text-align:center;padding:120px;color:gray;font-size:18px'>"
            "📌 Pin a location on the globe to see the chart"
            "</div>", unsafe_allow_html=True
        )
else:
    monthly_df, yearly_df = build_df(lat, lon)

    with col2:
        info_box.success(f"**Lat:** {lat:.4f}°  \n**Lon:** {lon:.4f}°")
        stats_box.markdown(f"""
**📊 Stats**
- **Min:** {yearly_df['AvgTemperature'].min():.2f}°C
- **Max:** {yearly_df['AvgTemperature'].max():.2f}°C
- **Mean:** {yearly_df['AvgTemperature'].mean():.2f}°C
- **Trend:** {"📈 Warming" if yearly_df['AvgTemperature'].iloc[-1] > yearly_df['AvgTemperature'].iloc[0] else "📉 Cooling"}
        """)

    # ── Yearly scatter ─────────────────────────────────────────────────────────
    fig = px.scatter(
        yearly_df,
        x="Year",
        y="AvgTemperature",
        title=f"🌡️ Annual Mean Temperature — Lat {lat:.2f}°, Lon {lon:.2f}°",
        color="AvgTemperature",
        color_continuous_scale="RdYlBu_r",
        labels={"AvgTemperature": "Avg Temp (°C)"},
        trendline="ols"
    )
    fig.update_traces(marker=dict(size=8))
    fig.update_layout(
        height=460,
        margin=dict(t=50, b=40, l=50, r=20),
        coloraxis_colorbar=dict(title="°C")
    )
    chart_placeholder.plotly_chart(fig, use_container_width=True)

    # ── Monthly scatter ────────────────────────────────────────────────────────
    fig2 = px.scatter(
        monthly_df,
        x="time",
        y="temperature",
        title="📅 Monthly Temperature — Full Record",
        color="temperature",
        color_continuous_scale="RdYlBu_r",
        labels={"temperature": "Temp (°C)", "time": "Month"},
    )
    fig2.update_traces(marker=dict(size=3))
    fig2.update_layout(
        height=300,
        margin=dict(t=50, b=40, l=50, r=20),
        coloraxis_colorbar=dict(title="°C"),
        xaxis=dict(tickangle=45, nticks=20)
    )
    monthly_placeholder.plotly_chart(fig2, use_container_width=True)

# ── Auto-refresh ──────────────────────────────────────────────────────────────
if auto_refresh:
    time.sleep(refresh_rate)
    st.rerun()
