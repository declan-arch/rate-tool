#!/usr/bin/env python3
"""
Rate Intelligence — Quick Run Script
Just answer a few questions and it does the rest.
"""

import json, os, sys, asyncio, re
from pathlib import Path
from datetime import datetime


# ── Auto-competitor lookup by area keyword ────────────────────────────────────
AREA_COMPETITORS = {
    "rosebank": [
        "The Tyrwhitt Rosebank",
        "Radisson Red Rosebank",
        "Clico Boutique Hotel Rosebank",
        "Hyatt Place Rosebank",
        "The Davinci Hotel and Suites Sandton",
        "Rosewood Johannesburg",
    ],
    "sandton": [
        "Radisson Blu Gautrain Hotel Sandton",
        "Saxon Hotel Villas and Spa Sandton",
        "Hyatt Regency Johannesburg",
        "The Maslow Hotel Sandton",
        "Protea Hotel Sandton",
        "InterContinental Johannesburg Sandton Towers",
    ],
    "midrand": [
        "Protea Hotel Midrand",
        "Gallagher Estate Hotel Midrand",
        "Southern Sun Midrand",
        "Peermont Metcourt Hotel Midrand",
        "Garden Court Midrand",
    ],
    "cape town": [
        "The Silo Hotel Cape Town",
        "One&Only Cape Town",
        "Radisson Blu Hotel Waterfront Cape Town",
        "Taj Cape Town",
        "The Cape Milner Hotel",
    ],
    "waterfront": [
        "The Silo Hotel Cape Town",
        "One&Only Cape Town",
        "Radisson Blu Hotel Waterfront Cape Town",
        "Taj Cape Town",
        "Protea Hotel Victoria Junction",
    ],
    "durban": [
        "Radisson Blu Hotel Durban Umhlanga",
        "Protea Hotel Durban Umhlanga",
        "Coastlands Umhlanga Hotel",
        "The Oyster Box Umhlanga",
        "Garden Court South Beach Durban",
    ],
}

DEFAULT_COMPETITORS = [
    "Radisson Red Johannesburg",
    "Protea Hotel Johannesburg",
    "Hyatt Place Johannesburg",
    "Southern Sun OR Tambo",
    "Garden Court Sandton City",
]


def get_auto_competitors(property_name: str, n: int = 5) -> list[str]:
    """Pick real competitor names based on area keywords in the property name."""
    name_lower = property_name.lower()
    for area, comps in AREA_COMPETITORS.items():
        if area in name_lower:
            # Exclude the target property itself from competitor list
            filtered = [c for c in comps if c.lower() not in name_lower and name_lower not in c.lower()]
            return filtered[:n]
    return DEFAULT_COMPETITORS[:n]

def ask(prompt, default=None):
    if default:
        val = input(f"{prompt} [{default}]: ").strip()
        return val if val else default
    else:
        val = input(f"{prompt}: ").strip()
        return val

def ask_yn(prompt, default="y"):
    val = input(f"{prompt} (y/n) [{default}]: ").strip().lower()
    return (val if val else default) == "y"

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def banner():
    print()
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("   RATE INTELLIGENCE AGENT  ·  by RevGrowth")
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()

def main():
    clear()
    banner()

    # ── Step 1: Property ──────────────────────────────────────────────────────
    print("  STEP 1 — Which property do you want to scan?\n")
    print("  Examples: The Tyrwhitt Rosebank")
    print("            Protea Hotel Midrand")
    print("            Fairlawns Boutique Hotel Sandton\n")
    property_name = ask("  Property name and location")
    if not property_name:
        print("  No property entered. Exiting.")
        sys.exit(1)

    print()
    print("  STEP 2 — Which channels? (press Enter to scan ALL)\n")
    print("  1 = Booking.com")
    print("  2 = Expedia")
    print("  3 = LekkeSlaap")
    print("  4 = Agoda")
    print("  5 = SA Venues")
    print("  all = All of the above\n")
    ch_input = ask("  Your choice", "all").lower()

    ch_map = {"1":"booking_com","2":"expedia","3":"lekkeslaap","4":"agoda","5":"sa_venues"}
    all_channels = ["booking_com","expedia","lekkeslaap","agoda","sa_venues"]
    if ch_input == "all" or ch_input == "":
        channels = all_channels
    else:
        channels = [ch_map[c.strip()] for c in ch_input.split(",") if c.strip() in ch_map]
        if not channels:
            channels = all_channels

    print()
    print("  STEP 3 — Do you want competitor data as well?\n")
    print("  This will search for nearby hotels and compare their rates.")
    print("  You can type your own competitors or let it find them automatically.\n")
    want_comps = ask_yn("  Include competitors", "y")

    comps = []
    if want_comps:
        print()
        print("  Type up to 5 competitor names (one per line).")
        print("  Press Enter on a blank line to skip and use automatic competitors.\n")
        for i in range(5):
            c = input(f"  Competitor {i+1} (or Enter to skip): ").strip()
            if not c:
                break
            comps.append(c)

    print()
    print("  STEP 4 — Check-in date\n")
    checkin = ask("  Check-in date (YYYY-MM-DD, or Enter for 7 days from now)", "")

    print()
    print("  STEP 5 — Anthropic API key\n")
    print("  This is used to interpret what the rates mean (NR, Flexible, B&B etc.)")
    print("  Get yours free at: https://console.anthropic.com\n")
    api_key_env = os.environ.get("ANTHROPIC_API_KEY","")
    if api_key_env:
        print(f"  ✓ Found API key in environment ({api_key_env[:12]}...)")
        api_key = api_key_env
    else:
        api_key = ask("  Paste your API key (starts with sk-ant-)", "")
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key

    # ── Build config ──────────────────────────────────────────────────────────
    print()
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Ready to scan: {property_name}")
    print(f"  Channels:      {', '.join(channels)}")
    print(f"  Competitors:   {'Yes ('+str(len(comps))+' named)' if comps else ('Yes (auto)' if want_comps else 'No')}")
    print(f"  LLM interpret: {'Yes' if api_key else 'No (raw data only)'}")
    print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    go = ask_yn("  Start scan now", "y")
    if not go:
        print("  Cancelled.")
        sys.exit(0)

    # Write a temporary config
    config = {
        "target_property": {
            "name": property_name,
            "location": "",
            "booking_com_search": property_name,
            "expedia_search": property_name,
            "agoda_search": property_name,
            "lekkeslaap_search": property_name,
            "sa_venues_search": property_name,
            "star_rating": 4,
            "room_types": {},
            "nightly_rates": {},
            "monthly_rates": {}
        },
        "competitor_set": {
            "geo_radius_km": 3,
            "star_rating_range": [3, 5],
            "node_comps": comps if comps else get_auto_competitors(property_name),
        },
        "scrape_config": {
            "check_in_offset_days": 7,
            "nights": 1,
            "adults": 2,
            "children": 0,
            "currency": "ZAR",
            "headless": True,
            "timeout_ms": 30000
        }
    }

    if checkin:
        # Convert offset to days
        from datetime import datetime, timedelta
        try:
            ci = datetime.strptime(checkin, "%Y-%m-%d")
            offset = (ci - datetime.today()).days
            config["scrape_config"]["check_in_offset_days"] = max(1, offset)
        except ValueError:
            print("  Invalid date format, using default (7 days from now)")

    config_path = Path(__file__).parent / "config" / "_run_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    print()
    print("  Scanning now — this takes 2-4 minutes per channel...")
    print("  You'll see progress below.\n")

    # Build the command args
    output_dir = Path(__file__).parent / "output"
    args = ["--channels"] + channels + ["--output-dir", str(output_dir)]
    if not want_comps:
        args.append("--target-only")
    if not api_key:
        args.append("--no-interpret")

    import subprocess
    cmd = [sys.executable, str(Path(__file__).parent / "main.py")] + args
    # Override config path via env and ensure API key is set
    env = os.environ.copy()
    env["RATE_TOOL_CONFIG"] = str(config_path)
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key

    result = subprocess.run(cmd, env=env)

    if result.returncode == 0:
        print()
        print("  ✓ Scan complete!")
        print(f"  Your report is in: {output_dir}")
        print("  Open the .xlsx file in Excel or Google Sheets.")
    else:
        print()
        print("  Something went wrong. Check the messages above for clues.")
        print("  Most common fix: make sure you have internet access and try again.")

if __name__ == "__main__":
    main()
