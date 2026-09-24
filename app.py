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
    page_title="Rate Intelligence",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Palette — black / white / mustard, minimal and editorial rather than decorative.
INK = "#111111"
INK_MUTED = "#6B6B66"
PAPER = "#FFFFFF"
PAPER_MUTED = "#FAFAF7"
LINE = "#E4E1D8"
MUSTARD = "#C9972B"
MUSTARD_DARK = "#A67D20"
RUST = "#B5651D"  # reserved for a single "more expensive" signal — everything else stays mono

CHART_LAYOUT = {
    "paper_bgcolor": PAPER,
    "plot_bgcolor": PAPER,
    "font": {"color": INK, "family": "Helvetica, Arial, sans-serif", "size": 13},
    "title": {"font": {"size": 15, "color": INK}},
    "xaxis": {"gridcolor": LINE, "zerolinecolor": LINE, "linecolor": LINE},
    "yaxis": {"gridcolor": LINE, "zerolinecolor": LINE, "linecolor": LINE},
    "margin": {"l": 40, "r": 20, "t": 50, "b": 40},
    "colorway": [MUSTARD, INK, "#9B9B93", RUST],
}

st.markdown(
    f"""
    <style>
    .stApp {{ background-color: {PAPER}; color: {INK}; }}
    html, body, [class*="css"] {{ font-family: Helvetica, Arial, sans-serif; }}

    [data-testid="stSidebar"] {{ background-color: {INK}; }}
    [data-testid="stSidebar"] * {{ color: {PAPER} !important; }}
    [data-testid="stSidebar"] label {{ font-weight: 600; letter-spacing: .02em; }}
    [data-testid="stSidebar"] hr {{ border-color: #333333; }}
    [data-testid="stSidebar"] input, [data-testid="stSidebar"] textarea {{
        background-color: #1C1C1C !important; border: 1px solid #333333 !important; color: {PAPER} !important;
    }}
    [data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small {{ color: #A3A39C !important; }}

    .ri-header {{ display: flex; align-items: baseline; justify-content: space-between; border-bottom: 2px solid {MUSTARD}; padding-bottom: 14px; margin-bottom: 28px; }}
    .ri-title {{ color: {INK}; font-size: 1.5rem; font-weight: 700; margin: 0; letter-spacing: -.01em; }}
    .ri-tagline {{ color: {INK_MUTED}; font-size: .85rem; margin: 2px 0 0; }}

    .ri-card {{ background-color: {PAPER_MUTED}; border: 1px solid {LINE}; border-radius: 4px; padding: 20px 24px; margin-bottom: 16px; }}
    .ri-card h3, .ri-card h4 {{ color: {INK}; margin-top: 0; font-weight: 700; }}
    .ri-card p, .ri-card li {{ color: {INK_MUTED}; }}

    .status-pill {{ border-radius: 3px; padding: 5px 14px; font-weight: 600; font-size: .8rem; display: inline-block; letter-spacing: .03em; text-transform: uppercase; }}
    .status-running {{ background-color: #FFFFFF; border: 1px solid {MUSTARD}; color: {MUSTARD_DARK}; }}
    .status-done {{ background-color: {INK}; border: 1px solid {INK}; color: {PAPER}; }}
    .status-error {{ background-color: #FFFFFF; border: 1px solid {RUST}; color: {RUST}; }}

    .ri-metric {{ background-color: {PAPER}; border: 1px solid {LINE}; border-top: 3px solid {MUSTARD}; border-radius: 3px; padding: 16px; text-align: left; }}
    .ri-metric-val {{ font-size: 1.5rem; font-weight: 700; color: {INK}; line-height: 1.1; }}
    .ri-metric-lbl {{ font-size: .72rem; color: {INK_MUTED}; margin-top: 4px; text-transform: uppercase; letter-spacing: .04em; }}

    .stButton > button {{ background-color: {MUSTARD}; color: {INK}; font-weight: 700; border: none; border-radius: 3px; padding: 10px 20px; width: 100%; letter-spacing: .01em; }}
    .stButton > button:hover {{ background-color: {MUSTARD_DARK}; color: {PAPER}; }}
    [data-testid="stSidebar"] .stButton > button {{ background-color: {MUSTARD}; color: {INK}; }}
    [data-testid="stSidebar"] .stButton > button:hover {{ background-color: #E0B24F; }}

    .stDownloadButton > button {{ background-color: {PAPER}; color: {INK}; border: 1px solid {INK}; font-weight: 600; border-radius: 3px; }}
    .stDownloadButton > button:hover {{ background-color: {INK}; color: {PAPER}; }}

    [data-testid="stTabs"] button[role="tab"] {{ font-weight: 600; color: {INK_MUTED}; }}
    [data-testid="stTabs"] button[aria-selected="true"] {{ color: {INK}; border-bottom-color: {MUSTARD} !important; }}

    hr {{ border-color: {LINE}; }}
    [data-testid="stDataFrame"] {{ border: 1px solid {LINE}; border-radius: 3px; }}
    </style>
    """,
    unsafe_allow_html=True,
)

ALLOWED_PASSWORDS = {"revgrowth2024", "rateiq", "tyrwhitt"}
CHANNEL_MAP = {
    "Booking.com": "booking_com",
    "Airbnb": "airbnb",
}
CHANNEL_LABELS_DISPLAY = {value: key for key, value in CHANNEL_MAP.items()}
CHANNEL_LABELS_DISPLAY["direct"] = "Direct Website"


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
        f"""
        <div style="max-width:400px;margin:100px auto 0;border-top:3px solid {MUSTARD};padding:32px 4px;text-align:center">
          <div style="color:{INK};font-size:1.3rem;font-weight:700;letter-spacing:-.01em">Rate Intelligence</div>
          <div style="color:{INK_MUTED};font-size:.82rem;margin-top:4px">South African Hospitality</div>
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
        from scrapers import AirbnbScraper, BookingScraper, DirectScraper

        scraper_map = {
            "booking_com": BookingScraper,
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
            direct_url = config["target_property"].get("direct_website_url", "").strip()
            if direct_url:
                push_status(f"Checking direct website: {direct_url}...")
                try:
                    scraper = DirectScraper(config)
                    direct_results = await scraper.run(direct_url, target_name)
                    if direct_results:
                        push_status(f"  → found {len(direct_results)} price(s) on direct site (best-effort).")
                    else:
                        push_status(f"  ⚠ no data — {scraper.last_diagnostic or 'unknown reason'}")
                    records.extend(direct_results)
                except Exception as error:
                    push_status(f"  ⚠ Direct website check failed: {error}")

            if not target_only:
                competitor_urls = config["competitor_set"]["competitor_urls"]
                push_status(f"Checking {len(competitor_urls)} competitor(s) — direct site + {', '.join(CHANNEL_LABELS_DISPLAY.get(c, c) for c in channels if c in scraper_map)}...")
                for comp_url in competitor_urls:
                    push_status(f"  → {comp_url}...")
                    comp_name = None
                    try:
                        scraper = DirectScraper(config)
                        comp_results = await scraper.run(comp_url)
                        if comp_results:
                            comp_name = comp_results[0]["property_name"]  # confirmed real, not a blocked-page title guess
                            push_status(f"    direct site: found {len(comp_results)} price(s) (best-effort).")
                        else:
                            push_status(f"    ⚠ direct site: no data — {scraper.last_diagnostic or 'unknown reason'}")
                        records.extend(comp_results)
                    except Exception as error:
                        push_status(f"  ⚠ {comp_url} failed: {error}")

                    # Also search Booking.com/Airbnb for this competitor by name — but only
                    # once we have a name confirmed from real page content (comp_name), not
                    # a guess derived from a blocked or property-less page.
                    if comp_name:
                        ota_results = await asyncio.gather(
                            *(scrape_one(channel, comp_name) for channel in channels if channel in scraper_map),
                            return_exceptions=True,
                        )
                        for channel, result in zip((c for c in channels if c in scraper_map), ota_results):
                            if isinstance(result, Exception):
                                push_status(f"    ⚠ {CHANNEL_LABELS_DISPLAY.get(channel, channel)}: {result}")
                            else:
                                push_status(f"    {CHANNEL_LABELS_DISPLAY.get(channel, channel)}: found {len(result)} rate(s).")
                                records.extend(result)
                    elif comp_url:
                        push_status(f"    (skipping Booking.com/Airbnb for this one — no confirmed name to search)")

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

        # Airbnb lists private rooms/apartments, not hotel inventory — its prices run far
        # lower than hotel BAR rates by nature, so the hotel-calibrated low_price threshold
        # (<R300) is not a real anomaly there. Strip it post-hoc rather than special-casing
        # every interpreter (LLM and rule-based both apply the same fixed threshold).
        for row in interpreted:
            if row.get("channel") == "airbnb":
                row["anomaly_flags"] = [f for f in row.get("anomaly_flags", []) if f != "low_price"]

        # The LLM classifies competitor_tier per 12-record batch with no visibility into
        # other batches, so a batch without the target's own rate in it has nothing to
        # compare against and defaults to UNKNOWN. Recompute tier globally, once, now that
        # the real target average is known across the whole scan — same logic rule_interpret
        # already applied internally, now applied uniformly regardless of which interpreter ran.
        from interpreter.rule_interpreter import classify_competitor_tier
        target_prices_all = [r["price_zar"] for r in interpreted if r.get("is_target_property") and r.get("price_zar")]
        target_avg_all = sum(target_prices_all) / len(target_prices_all) if target_prices_all else None
        for row in interpreted:
            if not row.get("is_target_property"):
                row["competitor_tier"] = classify_competitor_tier(row.get("price_zar"), target_avg_rate=target_avg_all, is_target=False)

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


def build_config(property_name, checkin_date, competitor_urls, direct_website_url=""):
    offset_days = max(1, (checkin_date - datetime.today().date()).days)
    return {
        "target_property": {
            "name": property_name,
            "direct_website_url": direct_website_url,
            "booking_com_search": property_name,
            "airbnb_search": property_name,
            "star_rating": 4,
            "room_types": {},
            "nightly_rates": {},
            "monthly_rates": {},
        },
        "competitor_set": {"competitor_urls": competitor_urls},
        "scrape_config": {"check_in_offset_days": offset_days, "nights": 1, "adults": 2, "children": 0, "currency": "ZAR", "headless": True, "timeout_ms": 30000},
    }


init_state()
if not st.session_state.authenticated:
    render_password_gate()
    st.stop()

with st.sidebar:
    st.markdown("### SCAN SETTINGS")
    st.markdown("---")
    property_name = st.text_input("Property name", placeholder="e.g. The Tyrwhitt Rosebank")
    direct_website_url = st.text_input("Hotel's direct website (optional)", placeholder="e.g. themonarchhotel.co.za", help="Best-effort rate check on the property's own booking site — every hotel site is built differently, so this is lower-confidence than the OTA scrapers.")
    st.markdown("**Channels**")
    channel_selections = {label: st.checkbox(label, value=True) for label in CHANNEL_MAP}
    selected_channels = [CHANNEL_MAP[label] for label, selected in channel_selections.items() if selected]
    checkin_date = st.date_input("Check-in date", value=datetime.today() + timedelta(days=7), min_value=datetime.today() + timedelta(days=1))
    include_competitors = st.toggle("Include competitors", value=True)
    competitor_urls = []
    if include_competitors:
        st.markdown("**Competitor website URLs**")
        st.caption("Same best-effort direct-site check as above, run once per competitor.")
        for index in range(5):
            comp_url = st.text_input(f"Competitor {index + 1} URL", key=f"competitor_url_{index}", placeholder="e.g. radissonhotels.com/...", label_visibility="collapsed")
            if comp_url.strip():
                competitor_urls.append(comp_url.strip())
    api_key = st.text_input("Anthropic API key", value=os.environ.get("ANTHROPIC_API_KEY", ""), type="password")
    st.markdown("---")
    can_run = bool(property_name.strip()) and bool(selected_channels) and st.session_state.run_state != "running"
    run_button = st.button("Run Scan", disabled=not can_run)
    if not property_name.strip():
        st.caption("Enter a property name to enable scanning.")
    st.markdown("---")
    with st.expander("View a local scan"):
        st.caption("Booking.com and other OTAs may block Streamlit Cloud's IP. For reliable results, run `python run.py` on your own machine, then load the resulting interpreted_rates_*.json here to share it.")
        uploaded = st.file_uploader("interpreted_rates_*.json", type="json", label_visibility="collapsed")
        if uploaded is not None and st.button("Load into dashboard"):
            try:
                loaded = json.loads(uploaded.getvalue().decode("utf-8"))
                loaded_target = next((row.get("property_name") for row in loaded if row.get("is_target_property")), "Your Property")
                excel_out = TOOL_DIR / "output" / f"Rate_Intelligence_{loaded_target.replace(' ', '_')}_loaded.xlsx"
                excel_out.parent.mkdir(parents=True, exist_ok=True)
                from reports import generate_report
                generate_report(loaded, str(excel_out), target_name=loaded_target)
                st.session_state.interpreted_data = loaded
                st.session_state.excel_path = str(excel_out)
                st.session_state.run_state = "done"
                st.session_state.error_message = None
                st.rerun()
            except Exception as error:
                st.error(f"Couldn't load file: {error}")
    st.markdown("---")
    st.caption("Rate Intelligence · v1.0")

st.markdown(f'<div class="ri-header"><div><div class="ri-title">Rate Intelligence</div><div class="ri-tagline">South African Hospitality — competitive rate tracking</div></div></div>', unsafe_allow_html=True)

if run_button:
    config = build_config(property_name.strip(), checkin_date, competitor_urls, direct_website_url.strip())
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
        st.markdown(f'<div class="ri-card"><h3>What this tool does</h3><p style="line-height:1.7">Scans South African OTA channels and direct hotel websites for live rates, compares them with selected competitors, and produces a downloadable report.</p><h4>How it works</h4><ol style="line-height:1.9"><li>Scrape live rate data from each channel.</li><li>Interpret rates and flag anomalies.</li><li>Report results in a filterable dashboard and workbook.</li></ol></div>', unsafe_allow_html=True)
    with right:
        st.markdown(f'<div class="ri-card"><h4>Quick start</h4><ol style="line-height:1.9"><li>Enter a property name and a check-in date.</li><li>Optionally add the hotel\'s direct website and competitor URLs.</li><li>Optionally provide an Anthropic API key.</li><li>Click <strong>Run Scan</strong>.</li></ol></div>', unsafe_allow_html=True)
elif state == "running":
    st.markdown('<div class="status-pill status-running">Scan in progress</div>', unsafe_allow_html=True)
    messages = st.session_state.get("status_messages", [])
    if messages:
        with st.expander("Live progress log", expanded=True):
            st.code("\n".join(messages))
    with st.spinner("Scanning channels — this takes 2–5 minutes..."):
        import time
        time.sleep(3)
        st.rerun()
elif state == "done":
    st.markdown('<div class="status-pill status-done">Scan complete</div>', unsafe_allow_html=True)
    interpreted = st.session_state.interpreted_data or []
    # Airbnb lists private rooms/apartments, not hotel inventory — no BB/DBB rate plans,
    # no real geographic constraint on its search, and a structurally different price
    # distribution. It's shown in its own tab, never blended into the headline
    # hotel-vs-hotel comparison.
    hotel_records = [row for row in interpreted if row.get("channel") != "airbnb"]
    airbnb_records = [row for row in interpreted if row.get("channel") == "airbnb"]
    target_prices = [row["price_zar"] for row in hotel_records if row.get("is_target_property") and row.get("price_zar")]
    competitor_prices = [row["price_zar"] for row in hotel_records if not row.get("is_target_property") and row.get("price_zar")]
    metrics = [len(hotel_records), len({row.get("channel") for row in hotel_records}), round(sum(target_prices) / len(target_prices)) if target_prices else "N/A", round(sum(competitor_prices) / len(competitor_prices)) if competitor_prices else "N/A", sum(bool(row.get("anomaly_flags")) for row in hotel_records)]
    labels = ["Total records", "Channels scraped", "Your avg rate", "Competitor avg", "Anomalies flagged"]
    columns = st.columns(5)
    for column, value, label in zip(columns, metrics, labels):
        with column:
            display = f"R {value:,}" if isinstance(value, int) and label in {"Your avg rate", "Competitor avg"} else value
            st.markdown(f'<div class="ri-metric"><div class="ri-metric-val">{display}</div><div class="ri-metric-lbl">{label}</div></div>', unsafe_allow_html=True)
    if airbnb_records:
        st.caption(f"{len(airbnb_records)} Airbnb listing(s) found but excluded from the stats above — see the Airbnb tab.")

    excel_path = st.session_state.excel_path
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go

    chart_data = pd.DataFrame(hotel_records) if hotel_records else pd.DataFrame()
    target_name = "Your Property"
    priced_data = pd.DataFrame()
    if not chart_data.empty:
        chart_data["channel_label"] = chart_data["channel"].map(CHANNEL_LABELS_DISPLAY).fillna(chart_data["channel"])
        target_rows = chart_data[chart_data["is_target_property"].fillna(False)]
        target_name = target_rows["property_name"].dropna().iloc[0] if not target_rows.empty else "Your Property"
        chart_data["property_name"] = chart_data["property_name"].fillna("Unknown")
        chart_data["price_zar"] = pd.to_numeric(chart_data["price_zar"], errors="coerce")
        priced_data = chart_data.dropna(subset=["price_zar"])

    tab_overview, tab_channels, tab_airbnb, tab_data = st.tabs(["Overview", "Channel Analysis", "Airbnb", "Data & Downloads"])

    with tab_overview:
        if priced_data.empty:
            st.info("No priced hotel-channel records yet — run a scan to populate this view.")
        else:
            property_averages = priced_data.groupby(["property_name", "is_target_property"], dropna=False)["price_zar"].mean().reset_index()
            property_averages["color"] = property_averages["is_target_property"].map({True: "Your property", False: "Competitor"}).fillna("Competitor")
            figure = px.bar(property_averages, x="property_name", y="price_zar", color="color", color_discrete_map={"Your property": MUSTARD, "Competitor": INK}, title=f"Rate Comparison — {target_name} vs Competitors", labels={"property_name": "Property", "price_zar": "Average price (ZAR)"})
            figure.update_layout(showlegend=False, **CHART_LAYOUT)
            st.plotly_chart(figure, use_container_width=True)

            chart_left, chart_right = st.columns(2)
            with chart_left:
                # Target's own records aren't a "position" relative to itself — exclude them
                # so the chart actually shows competitor cheaper/comparable/more-expensive spread.
                competitor_only = chart_data[~chart_data["is_target_property"].fillna(False)]
                tier_counts = competitor_only["competitor_tier"].fillna("UNKNOWN").value_counts().rename_axis("tier").reset_index(name="count")
                tier_colors = {"CHEAPER": INK, "COMPARABLE": MUSTARD, "MORE_EXPENSIVE": RUST, "UNKNOWN": "#C7C4B8"}
                if tier_counts.empty:
                    st.info("No competitor data to position yet.")
                else:
                    figure = go.Figure(go.Pie(labels=tier_counts["tier"], values=tier_counts["count"], hole=0.55, marker={"colors": [tier_colors.get(tier, "#C7C4B8") for tier in tier_counts["tier"]]}))
                    figure.update_layout(title="Competitor Positioning", showlegend=True, **CHART_LAYOUT)
                    st.plotly_chart(figure, use_container_width=True)
            with chart_right:
                rate_counts = chart_data["rate_type"].fillna("UNKNOWN").value_counts().rename_axis("rate_type").reset_index(name="count")
                figure = px.bar(rate_counts, x="count", y="rate_type", orientation="h", title="Rate Types Found", labels={"count": "Count", "rate_type": "Rate type"})
                figure.update_traces(marker_color=MUSTARD)
                figure.update_layout(**CHART_LAYOUT)
                st.plotly_chart(figure, use_container_width=True)

    with tab_channels:
        if priced_data.empty:
            st.info("No priced hotel-channel records yet — run a scan to populate this view.")
        else:
            channel_averages = priced_data.groupby(["channel_label", "property_name"], dropna=False)["price_zar"].mean().reset_index()
            figure = px.bar(channel_averages, x="channel_label", y="price_zar", color="property_name", barmode="group", title="Average Rate by Channel", labels={"channel_label": "Channel", "price_zar": "Average price (ZAR)"})
            figure.update_layout(**CHART_LAYOUT)
            st.plotly_chart(figure, use_container_width=True)

            figure = px.box(priced_data, x="property_name", y="price_zar", color="is_target_property", title="Price Distribution by Property", labels={"property_name": "Property", "price_zar": "Price (ZAR)", "is_target_property": "Target property"}, color_discrete_sequence=[INK, MUSTARD])
            figure.update_layout(showlegend=False, **CHART_LAYOUT)
            st.plotly_chart(figure, use_container_width=True)

    with tab_airbnb:
        if not airbnb_records:
            st.info("No Airbnb listings found in this scan.")
        else:
            st.caption("Airbnb lists private rooms/apartments, not hotel rooms — prices aren't directly comparable to hotel BAR rates and are excluded from the Overview/Channel Analysis tabs.")
            airbnb_df = pd.DataFrame(airbnb_records)
            airbnb_df["price_zar"] = pd.to_numeric(airbnb_df["price_zar"], errors="coerce")
            airbnb_priced = airbnb_df.dropna(subset=["price_zar"])
            if not airbnb_priced.empty:
                airbnb_avg = airbnb_priced.groupby("property_name", dropna=False)["price_zar"].mean().reset_index()
                figure = px.bar(airbnb_avg, x="property_name", y="price_zar", title="Airbnb Listings — Average Price", labels={"property_name": "Listing", "price_zar": "Average price (ZAR)"})
                figure.update_traces(marker_color=MUSTARD)
                figure.update_layout(**CHART_LAYOUT)
                st.plotly_chart(figure, use_container_width=True)
            st.dataframe(pd.DataFrame([{"Listing": row.get("property_name", ""), "Price (ZAR)": row.get("price_zar")} for row in airbnb_records]), use_container_width=True, hide_index=True)

    with tab_data:
        download_left, download_right = st.columns(2)
        with download_left:
            if excel_path and Path(excel_path).exists():
                with open(excel_path, "rb") as report_file:
                    st.download_button("Download Excel Report", report_file.read(), file_name=Path(excel_path).name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        with download_right:
            if interpreted:
                csv_bytes = pd.DataFrame(interpreted).to_csv(index=False).encode("utf-8")
                st.download_button("Download Raw Data (CSV)", csv_bytes, file_name="rate_intelligence_data.csv", mime="text/csv", use_container_width=True)

        if interpreted:
            table = pd.DataFrame([{"Property": row.get("property_name", ""), "Channel": CHANNEL_LABELS_DISPLAY.get(row.get("channel", ""), row.get("channel", "")), "Room Type": row.get("room_type", ""), "Rate Type": row.get("rate_type", ""), "Price (ZAR)": row.get("price_zar"), "Competitor Tier": row.get("competitor_tier", ""), "Confidence": row.get("confidence", ""), "Anomalies": ", ".join(row.get("anomaly_flags", [])) or "—"} for row in interpreted])
            st.markdown("#### Rate Summary Table")
            st.caption("Includes all channels, including Airbnb. Headline stats in Overview exclude Airbnb. Click a column header to sort, or use the filters below.")

            filter_left, filter_mid, filter_right, sort_col = st.columns(4)
            with filter_left:
                channel_filter = st.selectbox("Channel", ["All"] + sorted(table["Channel"].dropna().unique().tolist()))
            with filter_mid:
                rate_filter = st.selectbox("Rate type", ["All"] + sorted(table["Rate Type"].dropna().unique().tolist()))
            with filter_right:
                tier_filter = st.selectbox("Competitor tier", ["All"] + sorted(table["Competitor Tier"].dropna().unique().tolist()))
            with sort_col:
                sort_choice = st.selectbox("Sort by", ["Default", "Price (low to high)", "Price (high to low)", "Property (A–Z)"])

            if channel_filter != "All":
                table = table[table["Channel"] == channel_filter]
            if rate_filter != "All":
                table = table[table["Rate Type"] == rate_filter]
            if tier_filter != "All":
                table = table[table["Competitor Tier"] == tier_filter]
            if sort_choice == "Price (low to high)":
                table = table.sort_values("Price (ZAR)", ascending=True, na_position="last")
            elif sort_choice == "Price (high to low)":
                table = table.sort_values("Price (ZAR)", ascending=False, na_position="last")
            elif sort_choice == "Property (A–Z)":
                table = table.sort_values("Property", ascending=True, na_position="last")

            st.dataframe(table, use_container_width=True, height=420, hide_index=True)
            st.caption(f"Showing {len(table)} of {len(interpreted)} records.")
elif state == "error":
    st.markdown('<div class="status-pill status-error">Scan failed</div>', unsafe_allow_html=True)
    st.error(st.session_state.get("error_message", "Unknown error"))
    if st.button("Try Again"):
        st.session_state.run_state = "idle"
        st.session_state.status_messages = []
        st.session_state.error_message = None
        st.rerun()
