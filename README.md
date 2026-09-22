# Rate Intelligence Agent — The Tyrwhitt

Scrapes OTA rates for The Tyrwhitt (Rosebank) and competitor properties,
classifies them via LLM, and outputs a formatted Excel rate intelligence report.

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt --break-system-packages

# 2. Set your Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-..."

# 3. Run full scan (all channels + competitors)
python main.py

# 4. Target property only (no competitors)
python main.py --target-only

# 5. Single channel test
python main.py --channels booking_com --target-only

# 6. Dry run (re-use last scrape, re-interpret)
python main.py --dry-run

# 7. Skip LLM (raw data export only, no API cost)
python main.py --no-interpret
```

## Output

Reports land in `./output/`:
- `raw_rates_YYYYMMDD_HHMMSS.json`         — raw scraped data
- `interpreted_rates_YYYYMMDD_HHMMSS.json` — LLM-classified data
- `Rate_Intelligence_The_Tyrwhitt_YYYYMMDD_HHMMSS.xlsx` — formatted report

## Report Tabs

| Tab | Contents |
|-----|----------|
| Summary | KPIs: avg rates, channel breakdown, parity delta |
| All Rates | Full record list with colour-coded rate types + tier |
| Target Property | The Tyrwhitt rates across all channels |
| Competitor Analysis | Comps sorted by price with tier flag |
| Anomalies | Records with scrape issues or outlier prices |

## Channels

| Channel | URL |
|---------|-----|
| Booking.com | www.booking.com |
| Expedia | www.expedia.co.za |
| Agoda | www.agoda.com |
| LekkeSlaap | www.lekkeslaap.co.za |
| SA Venues | www.sa-venues.com |

## Rate Types

| Code | Meaning |
|------|---------|
| NR | Non-Refundable / Prepaid |
| FLEX | Flexible / Free Cancellation |
| BB | Bed & Breakfast |
| DBB | Dinner Bed & Breakfast / Half Board |
| SPA | Spa Inclusive |
| RO | Room Only (self-catering) |

## Room Types (The Tyrwhitt)

| Code | Label |
|------|-------|
| STD | Standard 1-bed, max 2 pax |
| LUX | Luxury 1-bed, max 2 pax |
| FAM_STD | Family Standard 2-bed, max 4 pax |
| FAM_LUX | Family Luxury 2-bed, max 4 pax |

## Competitor Tiers

| Tier | Meaning |
|------|---------|
| MORE_EXPENSIVE | Comp charges more than Tyrwhitt |
| COMPARABLE | Within ±15% of Tyrwhitt rack rate |
| CHEAPER | Comp charges less than Tyrwhitt |
| TARGET | This IS The Tyrwhitt record |

## Deploying to Google Cloud VM

```bash
# On your GCP VM:
git clone <your-repo> rate_tool
cd rate_tool
pip install -r requirements.txt --break-system-packages

# Install Playwright browser
playwright install chromium

# Set API key (or use Secret Manager)
export ANTHROPIC_API_KEY="sk-ant-..."

# Run daily via cron (e.g. 06:00 SAST = 04:00 UTC)
echo "0 4 * * * cd /home/ubuntu/rate_tool && python main.py >> /var/log/rate_tool.log 2>&1" | crontab -
```

## Project Structure

```
rate_tool/
├── main.py                    # orchestrator
├── requirements.txt
├── config/
│   ├── properties.json        # target property + competitor set
│   └── rate_taxonomy.json     # rate type + room type codes
├── scrapers/
│   ├── base_scraper.py        # shared Playwright base class
│   ├── booking_scraper.py     # Booking.com
│   ├── expedia_scraper.py     # Expedia
│   ├── agoda_scraper.py       # Agoda
│   ├── lekkeslaap_scraper.py  # LekkeSlaap
│   └── sa_venues_scraper.py   # SA Venues
├── interpreter/
│   └── llm_interpreter.py     # Claude API classification layer
├── reports/
│   └── excel_report.py        # openpyxl report builder
└── output/                    # generated reports (gitignored)
```
