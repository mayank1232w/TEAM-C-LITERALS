"""
HOT TODAY ?
Run:  streamlit run app.py
(ncep_data.json.gz and test8_updated.html must be in the same folder)
"""

import os, gzip, json, base64, threading, http.server, socketserver
import urllib.request, urllib.parse
import numpy as np, pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go

st.set_page_config(
    page_title="HOT TODAY?",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DATA_FILE = "ncep_data.json.gz"
DATA_PORT  = 18432

# ── Build patched globe HTML — plain function, no Streamlit deps ──────────
def _build_globe_html(port):
    with open("test8_updated.html", "r", encoding="utf-8") as f:
        html = f.read()

    # 1. Redirect gz fetch to local server
    html = html.replace(
        "await fetch('/ncep_data.json.gz')",
        f"await fetch('http://localhost:{port}/ncep_data.json.gz')"
    )

    # 2. Remove Flask /location POST + window.open block
    flask_block = (
        "  // \u2500\u2500 Send lat/lon to Flask \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        "  fetch('/location', {\n"
        "    method: 'POST',\n"
        "    headers: { 'Content-Type': 'application/json' },\n"
        "    body: JSON.stringify({ lat: lat, lon: lon })\n"
        "  })\n"
        "  .then(r => r.json())\n"
        "  .then(data => {\n"
        "    console.log('Flask received:', data)\n"
        "    // Open Streamlit scatter plot in a popup window\n"
        "    window.open(\n"
        "      'http://localhost:8501',\n"
        "      'ScatterPlot',\n"
        "      'width=900,height=650,resizable=yes,scrollbars=yes,toolbar=no,menubar=no'\n"
        "    )\n"
        "  })\n"
        "  .catch(err => console.error('Flask error:', err))"
    )
    html = html.replace(flask_block, "  // removed")

    # 3. Redirect revgeo through our local proxy (bypasses browser sandbox)
    old_revgeo = (
        "async function revgeo(lat,lon){try{const r=await fetch(`https://nominatim.openstreetmap.org"
        "/reverse?format=json&lat=${lat}&lon=${lon}`,{headers:{'Accept-Language':'en'}});"
        "const d=await r.json(),a=d.address||{};return(a.city||a.town||a.village||a.county||'')"
        "+(a.country?', '+a.country:'')}catch{return''}}"
    )
    new_revgeo = (
        f"async function revgeo(lat,lon){{try{{const r=await fetch("
        f"`http://localhost:{port}/revgeo?lat=${{lat}}&lon=${{lon}}`);"
        f"const d=await r.json(),a=d.address||{{}};"
        f"return(a.city||a.town||a.village||a.county||'')+(a.country?', '+a.country:'')"
        f"}}catch(e){{return ''}}}}"
    )
    html = html.replace(old_revgeo, new_revgeo)

    return html.encode("utf-8")


# ── Pre-build the globe HTML once at module load (safe for server thread) ─
_GLOBE_HTML_BYTES = _build_globe_html(DATA_PORT)


# ── Local HTTP server ─────────────────────────────────────────────────────
def _start_file_server():
    cwd = os.getcwd()
    globe_bytes = _GLOBE_HTML_BYTES   # captured at startup, no Streamlit calls

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path

            if path == "/globe":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(globe_bytes)

            elif path == "/ncep_data.json.gz":
                fpath = os.path.join(cwd, DATA_FILE)
                with open(fpath, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            elif path == "/revgeo":
                qs     = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(qs)
                try:
                    lat = params["lat"][0]
                    lon = params["lon"][0]
                    url = (f"https://nominatim.openstreetmap.org/reverse"
                           f"?format=json&lat={lat}&lon={lon}")
                    req = urllib.request.Request(url, headers={
                        "Accept-Language": "en",
                        "User-Agent": "NCEPClimateExplorer/1.0"
                    })
                    with urllib.request.urlopen(req, timeout=6) as r:
                        body = r.read()
                except Exception:
                    body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_): pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", DATA_PORT), Handler) as srv:
        srv.serve_forever()

if "file_server_started" not in st.session_state:
    threading.Thread(target=_start_file_server, daemon=True).start()
    st.session_state["file_server_started"] = True


# ── Pre-compute annual global mean temperature ────────────────────────────
@st.cache_data(show_spinner="Loading data…")
def load_annual():
    with gzip.open(DATA_FILE, "rb") as f:
        NC = json.loads(f.read())

    tmin, tmax = NC["tmin"], NC["tmax"]
    nlat, nlon = NC["nlat"], NC["nlon"]
    nt, times  = NC["nt"], NC["times"]
    lats       = np.array(NC["lats"])
    SPAN       = tmax - tmin

    raw   = np.frombuffer(base64.b64decode(NC["data_b64"]),
                          dtype=np.uint8).reshape(nt, nlat, nlon)
    temps = tmin + (raw / 255.0) * SPAN

    weights = np.cos(np.radians(lats))
    w2d     = np.tile(weights[:, None], (1, nlon)).flatten()
    gmean   = np.average(temps.reshape(nt, -1), weights=w2d, axis=1)

    df  = pd.DataFrame({"year": [t[:4] for t in times], "temp": gmean})
    ann = df.groupby("year")["temp"].mean().reset_index()
    ann["year_int"] = ann["year"].astype(int)
    ann = ann[ann["year"] <= "2025"].reset_index(drop=True)
    return ann


# ── CSS ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
section[data-testid="stMain"] > div { background: #000 !important; }
[data-testid="stHeader"]            { background: transparent !important; }
[data-testid="stToolbar"],
[data-testid="stDecoration"],
footer                              { display: none !important; }
.block-container                    { padding: 0 !important; max-width: 100% !important; }

div[data-testid="stButton"] > button {
    background: rgba(0,0,0,0.62) !important;
    border: 1px solid rgba(255,255,255,0.14) !important;
    color: rgba(255,255,255,0.7) !important;
    font-family: 'Space Mono', monospace !important;
    font-size: 10px !important;
    letter-spacing: 0.05em !important;
    padding: 3px 9px !important;
    line-height: 1.1 !important;
    border-radius: 7px !important;
    width: auto !important;
    position: fixed !important;
    top: 140px !important;
    right: 20px !important;
    z-index: 9999 !important;
    backdrop-filter: blur(12px) !important;
    white-space: nowrap !important;
    transition: background 0.15s, border-color 0.15s !important;
}
div[data-testid="stButton"] > button:hover {
    background: rgba(255,107,53,0.25) !important;
    border-color: rgba(255,107,53,0.5) !important;
    color: #fff !important;
}
</style>
""", unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────
if "show_graph" not in st.session_state:
    st.session_state["show_graph"] = False

if st.button("📈 View Graph"):
    st.session_state["show_graph"] = True

# ── Globe via unsandboxed iframe ──────────────────────────────────────────
components.html(
    f'<iframe src="http://localhost:{DATA_PORT}/globe" '
    f'width="100%" height="750" frameborder="0" '
    f'style="display:block;border:none;"></iframe>',
    height=755, scrolling=False
)


# ── Scatter plot popup ────────────────────────────────────────────────────
@st.dialog("📈  Global Temperature vs Year", width="large")
def show_scatter():
    ann = load_annual()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ann["year_int"], y=ann["temp"],
        mode="markers+lines",
        marker=dict(
            color=ann["temp"],
            colorscale=[
                [0.0, "#3a7fd5"], [0.35, "#76b7fa"],
                [0.5, "#ffe066"], [0.65, "#ff8c42"], [1.0, "#dc3c28"],
            ],
            size=8,
            line=dict(color="rgba(255,255,255,0.2)", width=0.6),
            colorbar=dict(
                title=dict(text="°C", font=dict(color="rgba(255,255,255,0.6)", size=11)),
                tickfont=dict(color="rgba(255,255,255,0.5)", size=10),
                thickness=12, len=0.8,
            ),
            showscale=True,
        ),
        line=dict(color="rgba(255,255,255,0.12)", width=1),
        hovertemplate="<b>%{x}</b><br>Temp: %{y:.3f} °C<extra></extra>",
    ))
    fig.update_layout(
        paper_bgcolor="#050710", plot_bgcolor="#050710",
        font=dict(family="Space Mono, monospace", color="rgba(255,255,255,0.7)", size=11),
        margin=dict(l=60, r=20, t=20, b=55),
        height=460, hovermode="x unified", showlegend=False,
        hoverlabel=dict(bgcolor="#0d1220", bordercolor="rgba(255,107,53,0.5)",
                        font=dict(color="#fff", size=11)),
        xaxis=dict(title="Year", tickmode="linear", dtick=10,
                   gridcolor="rgba(255,255,255,0.06)", tickfont=dict(size=10),
                   title_font=dict(size=12)),
        yaxis=dict(title="Global Mean Temperature (°C)",
                   gridcolor="rgba(255,255,255,0.06)", tickfont=dict(size=10),
                   title_font=dict(size=12)),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

if st.session_state["show_graph"]:
    show_scatter()
    st.session_state["show_graph"] = False
