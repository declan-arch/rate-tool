#!/usr/bin/env python3
"""Rate Intelligence Streamlit web app."""

import asyncio
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx

TOOL_DIR = Path(__file__).parent
sys.path.insert(0, str(TOOL_DIR))


@st.cache_resource
def _ensure_playwright_chromium():
    """Download Chromium once per container — Streamlit Cloud never runs `playwright install` on its own."""
    try:
        subprocess.run(
            ["playwright", "install", "chromium"],
            check=True, capture_output=True, text=True, timeout=300,
        )
    except Exception as error:
        logging.error(f"playwright install chromium failed: {error}")


_ensure_playwright_chromium()

st.set_page_config(
    page_title="Rate Intelligence · RevGrowth",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp { background-color: #0D1B2A; color: #F0F0F0; }
    [data-testid="stSidebar"] { background-color: #111E2E; border-right: 1px solid #1E3A5F; }
    [data-testid="stSidebar"] label { color: #C9A84C !important; font-weight: 600; }
    .rg-header { background: linear-gradient(135deg, #0D1B2A, #1E3A5F); border: 1px solid #C9A84C; border-radius: 8px; padding: 24px 32px; margin-bottom: 24px; }
    .rg-title { color: #C9A84C; font-size: 2rem; font-weight: 700; margin: 0; }
    .rg-tagline { color: #2A9D8F; font-size: 1rem; margin: 4px 0 0; }
    .rg-card { background-color: #111E2E; border: 1px solid #1E3A5F; border-radius: 8px; padding: 20px; margin-bottom: 16px; }
    .status-running, .status-done, .status-error { border-radius: 20px; padding: 6px 16px; font-weight: 600; display: inline-block; }
    .status-running { background-color: #2A9D8F22; border: 1px solid #2A9D8F; color: #2A9D8F; }
    .status-done { background-color: #27AE6022; border: 1px solid #27AE60; color: #27AE60; }
    .status-error { background-color: #C0392B22; border: 1px solid #C0392B; color: #C0392B; }
    .rg-metric { background-color: #0D1B2A; border: 1px solid #1E3A5F; border-radius: 6px; padding: 14px; text-align: center; }
    .rg-metric-val { font-size: 1.6rem; font-weight: 700; color: #C9A84C; }
    .rg-metric-lbl { font-size: 0.78rem; color: #8FA8C0; margin-top: 2px; }
    .stButton > button { background-color: #C9A84C; color: #0D1B2A; font-weight: 700; border: none; border-radius: 6px; padding: 10px 24px; width: 100%; }
    .stButton > button:hover { background-color: #E8C46A; color: #0D1B2A; }
    hr { border-color: #1E3A5F; }
    </style>
    """,
    unsafe_allow_html=True,
)

ALLOWED_PASSWORDS = {"revgrowth2024", "rateiq", "tyrwhitt"}
CHANNEL_MAP = {
    "Booking.com": "booking_com",
    "Agoda": "agoda",
    "LekkeSlaap": "lekkeslaap",
    "SA Venues": "sa_venues",
    "Nightsbridge": "nightsbridge",
    "Airbnb": "airbnb",
}
CHANNEL_LABELS_DISPLAY = {value: key for key, value in CHANNEL_MAP.items()}
AREA_COMPETITORS = {
    "rosebank": ["Radisson Red Rosebank", "Clico Boutique Hotel Rosebank", "Hyatt Place Rosebank", "The Davinci Hotel and Suites Sandton", "Rosewood Johannesburg"],
    "sandton": ["Radisson Blu Gautrain Hotel Sandton", "Saxon Hotel Villas and Spa Sandton", "Hyatt Regency Johannesburg", "The Maslow Hotel Sandton", "InterContinental Johannesburg Sandton Towers"],
    "midrand": ["Protea Hotel Midrand", "Gallagher Estate Hotel Midrand", "Southern Sun Midrand", "Peermont Metcourt Hotel Midrand", "Garden Court Midrand"],
    "cape town": ["The Silo Hotel Cape Town", "One&Only Cape Town", "Radisson Blu Hotel Waterfront Cape Town", "Taj Cape Town", "The Cape Milner Hotel"],
    "waterfront": ["The Silo Hotel Cape Town", "One&Only Cape Town", "Radisson Blu Hotel Waterfront Cape Town", "Taj Cape Town", "Protea Hotel Victoria Junction"],
    "durban": ["Radisson Blu Hotel Durban Umhlanga", "Protea Hotel Durban Umhlanga", "Coastlands Umhlanga Hotel", "The Oyster Box Umhlanga", "Garden Court South Beach Durban"],
}
DEFAULT_COMPETITORS = ["Radisson Red Johannesburg", "Protea Hotel Johannesburg", "Hyatt Place Johannesburg", "Southern Sun OR Tambo", "Garden Court Sandton City"]


def get_auto_competitors(property_name, count=5):
    name_lower = property_name.lower()
    for area, competitors in AREA_COMPETITORS.items():
        if area in name_lower:
            filtered = [item for item in competitors if item.lower() not in name_lower and name_lower not in item.lower()]
            return filtered[:count]
    return DEFAULT_COMPETITORS[:count]


def init_state():
    defaults = {
        "authenticated": False,
        "run_state": "idle",
        "status_messages": [],
        "excel_path": None,
        "interpreted_data": None,
        "error_message": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_password_gate():
    st.markdown(
        """
        <div style="max-width:420px;margin:80px auto 0;background:#111E2E;border:1px solid #C9A84C;border-radius:10px;padding:40px 36px;text-align:center">
          <div style="font-size:2.2rem">📊</div>
          <div style="color:#C9A84C;font-size:1.4rem;font-weight:700">Rate Intelligence</div>
          <div style="color:#2A9D8F;font-size:.9rem">RevGrowth · South African Hospitality</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _, column, _ = st.columns([1, 2, 1])
    with column:
        password = st.text_input("Access password", type="password", placeholder="Enter password to continue")
        if st.button("Unlock"):
            if password in ALLOWED_PASSWORDS:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect password. Please try again.")


def push_status(message):
    try:
        st.session_state.status_messages.append(message)
    except (AttributeError, KeyError):
        logging.info(message)


def run_scan_thread(config, channels, target_only, api_key, output_dir):
    try:
        from interpreter import LLMInterpreter
        from interpreter.rule_interpreter import rule_interpret
        from reports import generate_report
        from scrapers import AgodaScraper, AirbnbScraper, BookingScraper, ExpediaScraper, LekkeSlaapScraper, NightsbridgeScraper, SAVenuesScraper

        scraper_map = {
            "booking_com": BookingScraper,
            "expedia": ExpediaScraper,
            "agoda": AgodaScraper,
            "lekkeslaap": LekkeSlaapScraper,
            "sa_venues": SAVenuesScraper,
            "nightsbridge": NightsbridgeScraper,
            "airbnb": AirbnbScraper,
        }
        target_name = config["target_property"]["name"]
        run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir.mkdir(parents=True, exist_ok=True)

        async def scrape_all():
            search_term = config["target_property"]["booking_com_search"]
            push_status(f"Scraping {target_name} across {len(channels)} channel(s)...")

            async def scrape_one(channel, term):
                push_status(f"  → {CHANNEL_LABELS_DISPLAY.get(channel, channel)}...")
                return await scraper_map[channel](config).run(term)

            results = await asyncio.gather(
                *(scrape_one(channel, search_term) for channel in channels if channel in scraper_map),
                return_exceptions=True,
            )
            records = []
            for channel, result in zip(channels, results):
                if isinstance(result, Exception):
                    push_status(f"  ⚠ {CHANNEL_LABELS_DISPLAY.get(channel, channel)} scraper error: {result}")
                else:
                    records.extend(result)
            if not target_only:
                competitors = config["competitor_set"]["node_comps"]
                push_status(f"Scraping {len(competitors)} competitor(s)...")
                for competitor in competitors:
                    push_status(f"  → {competitor}...")
                    competitor_results = await asyncio.gather(
                        *(scrape_one(channel, competitor) for channel in channels if channel in scraper_map),
                        return_exceptions=True,
                    )
                    records.extend(result for result in competitor_results if not isinstance(result, Exception) for result in result)
            return records

        raw_records = asyncio.run(scrape_all())
        raw_path = output_dir / f"raw_rates_{run_timestamp}.json"
        raw_path.write_text(json.dumps(raw_records, indent=2, default=str))
        push_status(f"Scrape complete — {len(raw_records)} raw records collected.")
        if not raw_records:
            st.session_state.error_message = "No data was scraped. Check internet connectivity and try again."
            st.session_state.run_state = "error"
            return

        push_status("Interpreting rates...")
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key
            try:
                interpreted = LLMInterpreter(api_key=api_key, target_name=target_name).interpret(raw_records)
                push_status(f"  → LLM interpretation complete ({len(interpreted)} records).")
            except Exception as error:
                push_status(f"  ⚠ LLM failed ({error}), falling back to rule-based classification...")
                interpreted = rule_interpret(raw_records, target_name=target_name)
        else:
            push_status("  → No API key — using rule-based classification...")
            interpreted = rule_interpret(raw_records, target_name=target_name)

        interp_path = output_dir / f"interpreted_rates_{run_timestamp}.json"
        interp_path.write_text(json.dumps(interpreted, indent=2, default=str))
        push_status("Generating Excel report...")
        excel_path = output_dir / f"Rate_Intelligence_{target_name.replace(' ', '_')}_{run_timestamp}.xlsx"
        generate_report(interpreted, str(excel_path), target_name=target_name)
        push_status(f"Report saved: {excel_path.name}")
        st.session_state.excel_path = str(excel_path)
        st.session_state.interpreted_data = interpreted
        st.session_state.run_state = "done"
    except Exception as error:
        logging.exception("Scan thread error")
        st.session_state.error_message = str(error)
        st.session_state.run_state = "error"


def build_config(property_name, checkin_date, competitors):
    offset_days = max(1, (checkin_date - datetime.today().date()).days)
    return {
        "target_property": {
            "name": property_name,
            "location": "",
            "booking_com_search": property_name,
            "expedia_search": property_name,
            "agoda_search": property_name,
            "lekkeslaap_search": property_name,
            "sa_venues_search": property_name,
            "nightsbridge_search": property_name,
            "airbnb_search": property_name,
            "star_rating": 4,
            "room_types": {},
            "nightly_rates": {},
            "monthly_rates": {},
        },
        "competitor_set": {"geo_radius_km": 3, "star_rating_range": [3, 5], "node_comps": competitors},
        "scrape_config": {"check_in_offset_days": offset_days, "nights": 1, "adults": 2, "children": 0, "currency": "ZAR", "headless": True, "timeout_ms": 30000},
    }


init_state()
if not st.session_state.authenticated:
    render_password_gate()
    st.stop()

with st.sidebar:
    st.markdown("### ⚙️ Scan Settings")
    st.markdown("---")
    property_name = st.text_input("Property name", placeholder="e.g. The Tyrwhitt Rosebank")
    st.markdown("**Channels**")
    channel_selections = {label: st.checkbox(label, value=True) for label in CHANNEL_MAP}
    selected_channels = [CHANNEL_MAP[label] for label, selected in channel_selections.items() if selected]
    checkin_date = st.date_input("Check-in date", value=datetime.today() + timedelta(days=7), min_value=datetime.today() + timedelta(days=1))
    include_competitors = st.toggle("Include competitors", value=True)
    competitors = []
    if include_competitors:
        st.markdown("**Competitor names** *(blank = auto-detect)*")
        for index in range(5):
            competitor = st.text_input(f"Competitor {index + 1}", key=f"competitor_{index}", placeholder="Leave blank for auto-detect", label_visibility="collapsed")
            if competitor.strip():
                competitors.append(competitor.strip())
    api_key = st.text_input("Anthropic API key", value=os.environ.get("ANTHROPIC_API_KEY", ""), type="password")
    st.markdown("---")
    can_run = bool(property_name.strip()) and bool(selected_channels) and st.session_state.run_state != "running"
    run_button = st.button("🚀 Run Scan", disabled=not can_run)
    if not property_name.strip():
        st.caption("⬆ Enter a property name to enable scanning.")
    st.markdown("---")
    st.caption("RevGrowth Rate Intelligence v1.0")

st.markdown('<div class="rg-header"><div class="rg-title">📊 RevGrowth Rate Intelligence</div><div class="rg-tagline">Rate Intelligence for South African Hospitality</div></div>', unsafe_allow_html=True)

if run_button:
    competitors_final = competitors if competitors else get_auto_competitors(property_name)
    config = build_config(property_name.strip(), checkin_date, competitors_final)
    (TOOL_DIR / "config" / "_run_config.json").write_text(json.dumps(config, indent=2))
    st.session_state.run_state = "running"
    st.session_state.status_messages = []
    st.session_state.excel_path = None
    st.session_state.interpreted_data = None
    st.session_state.error_message = None
    thread = threading.Thread(target=run_scan_thread, args=(config, selected_channels, not include_competitors, api_key, TOOL_DIR / "output"), daemon=True)
    add_script_run_ctx(thread)
    thread.start()
    st.rerun()

state = st.session_state.run_state
if state == "idle":
    left, right = st.columns([3, 2])
    with left:
        st.markdown('<div class="rg-card"><h3 style="color:#C9A84C;margin-top:0">What this tool does</h3><p style="color:#C0C8D0;line-height:1.7">The <strong>Rate Intelligence Agent</strong> scans South African OTA channels for live property rates, compares them with selected competitors, and produces an Excel report.</p><h4 style="color:#2A9D8F">How it works</h4><ol style="color:#C0C8D0;line-height:1.9"><li><strong>Scrape</strong> live rate cards from each OTA.</li><li><strong>Interpret</strong> rates and flag anomalies.</li><li><strong>Report</strong> results in a colour-coded workbook.</li></ol></div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="rg-card"><h4 style="color:#C9A84C;margin-top:0">Quick start</h4><ol style="color:#C0C8D0;line-height:1.9"><li>Enter a property name.</li><li>Select channels and a check-in date.</li><li>Add competitors or use auto-detection.</li><li>Optionally provide an Anthropic API key.</li><li>Click <strong>Run Scan</strong>.</li></ol></div>', unsafe_allow_html=True)
elif state == "running":
    st.markdown('<div class="status-running">⏳ Scan in progress</div>', unsafe_allow_html=True)
    messages = st.session_state.get("status_messages", [])
    if messages:
        with st.expander("Live progress log", expanded=True):
            st.code("\n".join(messages))
    with st.spinner("Scanning OTA channels — this takes 2–5 minutes..."):
        import time
        time.sleep(3)
        st.rerun()
elif state == "done":
    st.markdown('<div class="status-done">✅ Scan complete</div>', unsafe_allow_html=True)
    interpreted = st.session_state.interpreted_data or []
    prices = [row.get("price_zar") for row in interpreted if row.get("price_zar")]
    target_prices = [row["price_zar"] for row in interpreted if row.get("is_target_property") and row.get("price_zar")]
    competitor_prices = [row["price_zar"] for row in interpreted if not row.get("is_target_property") and row.get("price_zar")]
    metrics = [len(interpreted), len({row.get("channel") for row in interpreted}), round(sum(target_prices) / len(target_prices)) if target_prices else "N/A", round(sum(competitor_prices) / len(competitor_prices)) if competitor_prices else "N/A", sum(bool(row.get("anomaly_flags")) for row in interpreted)]
    labels = ["Total records", "Channels scraped", "Your avg rate", "Competitor avg", "Anomalies flagged"]
    columns = st.columns(5)
    for column, value, label in zip(columns, metrics, labels):
        with column:
            display = f"R {value:,}" if isinstance(value, int) and label in {"Your avg rate", "Competitor avg"} else value
            st.markdown(f'<div class="rg-metric"><div class="rg-metric-val">{display}</div><div class="rg-metric-lbl">{label}</div></div>', unsafe_allow_html=True)
    excel_path = st.session_state.excel_path
    if interpreted:
        import pandas as pd
        import plotly.express as px
        import plotly.graph_objects as go

        chart_data = pd.DataFrame(interpreted)
        chart_data["channel_label"] = chart_data["channel"].map(CHANNEL_LABELS_DISPLAY).fillna(chart_data["channel"])
        target_rows = chart_data[chart_data["is_target_property"].fillna(False)]
        target_name = target_rows["property_name"].dropna().iloc[0] if not target_rows.empty else "Your Property"
        chart_data["property_name"] = chart_data["property_name"].fillna("Unknown")
        chart_data["price_zar"] = pd.to_numeric(chart_data["price_zar"], errors="coerce")
        priced_data = chart_data.dropna(subset=["price_zar"])
        layout = {
            "paper_bgcolor": "#0D1B2A",
            "plot_bgcolor": "#0D1B2A",
            "font": {"color": "#F0F0F0"},
            "xaxis": {"gridcolor": "#1E3A5F", "zerolinecolor": "#1E3A5F"},
            "yaxis": {"gridcolor": "#1E3A5F", "zerolinecolor": "#1E3A5F"},
            "margin": {"l": 40, "r": 20, "t": 55, "b": 40},
        }

        property_averages = priced_data.groupby(["property_name", "is_target_property"], dropna=False)["price_zar"].mean().reset_index()
        property_averages["color"] = property_averages["is_target_property"].map({True: "Your property", False: "Competitor"}).fillna("Competitor")
        figure = px.bar(property_averages, x="property_name", y="price_zar", color="color", color_discrete_map={"Your property": "#C9A84C", "Competitor": "#2E86AB"}, title=f"Rate Comparison — {target_name} vs Competitors", labels={"property_name": "Property", "price_zar": "Average price (ZAR)"})
        figure.update_layout(showlegend=False, **layout)
        st.plotly_chart(figure, use_container_width=True)

        chart_left, chart_right = st.columns(2)
        with chart_left:
            tier_counts = chart_data["competitor_tier"].fillna("UNKNOWN").value_counts().rename_axis("tier").reset_index(name="count")
            tier_colors = {"CHEAPER": "#2DC653", "COMPARABLE": "#C9A84C", "MORE_EXPENSIVE": "#E63946", "UNKNOWN": "#6C757D"}
            figure = go.Figure(go.Pie(labels=tier_counts["tier"], values=tier_counts["count"], hole=0.5, marker={"colors": [tier_colors.get(tier, "#6C757D") for tier in tier_counts["tier"]]}))
            figure.update_layout(title="Competitor Positioning", showlegend=True, **layout)
            st.plotly_chart(figure, use_container_width=True)
        with chart_right:
            rate_counts = chart_data["rate_type"].fillna("UNKNOWN").value_counts().rename_axis("rate_type").reset_index(name="count")
            figure = px.bar(rate_counts, x="count", y="rate_type", orientation="h", title="Rate Types Found", labels={"count": "Count", "rate_type": "Rate type"})
            figure.update_traces(marker_color="#2A9D8F")
            figure.update_layout(**layout)
            st.plotly_chart(figure, use_container_width=True)

        channel_averages = priced_data.groupby(["channel_label", "property_name"], dropna=False)["price_zar"].mean().reset_index()
        figure = px.bar(channel_averages, x="channel_label", y="price_zar", color="property_name", barmode="group", title="Average Rate by Channel", labels={"channel_label": "Channel", "price_zar": "Average price (ZAR)"})
        figure.update_layout(**layout)
        st.plotly_chart(figure, use_container_width=True)

        figure = px.box(priced_data, x="property_name", y="price_zar", color="is_target_property", title="Price Distribution by Property", labels={"property_name": "Property", "price_zar": "Price (ZAR)", "is_target_property": "Target property"})
        figure.update_layout(showlegend=False, **layout)
        st.plotly_chart(figure, use_container_width=True)

    if excel_path and Path(excel_path).exists():
        with open(excel_path, "rb") as report_file:
            st.download_button("⬇️ Download Excel Report", report_file.read(), file_name=Path(excel_path).name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if interpreted:
        table = pd.DataFrame([{"Property": row.get("property_name", ""), "Channel": CHANNEL_LABELS_DISPLAY.get(row.get("channel", ""), row.get("channel", "")), "Room Type": row.get("room_type", ""), "Rate Type": row.get("rate_type", ""), "Price (ZAR)": row.get("price_zar"), "Competitor Tier": row.get("competitor_tier", ""), "Confidence": row.get("confidence", ""), "Anomalies": ", ".join(row.get("anomaly_flags", [])) or "—"} for row in interpreted])
        st.markdown("### Rate Summary Table")
        channel_filter = st.selectbox("Filter by channel", ["All"] + sorted(table["Channel"].dropna().unique().tolist()))
        rate_filter = st.selectbox("Filter by rate type", ["All"] + sorted(table["Rate Type"].dropna().unique().tolist()))
        if channel_filter != "All":
            table = table[table["Channel"] == channel_filter]
        if rate_filter != "All":
            table = table[table["Rate Type"] == rate_filter]
        st.dataframe(table, use_container_width=True, height=420, hide_index=True)
elif state == "error":
    st.markdown('<div class="status-error">❌ Scan failed</div>', unsafe_allow_html=True)
    st.error(st.session_state.get("error_message", "Unknown error"))
    if st.button("🔄 Try Again"):
        st.session_state.run_state = "idle"
        st.session_state.status_messages = []
        st.session_state.error_message = None
        st.rerun()
