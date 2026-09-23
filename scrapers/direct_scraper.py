"""
Direct website scraper — best-effort rate extraction from a hotel's own booking
site. Unlike the OTA scrapers, there's no shared DOM to target (every property
runs a different CMS/booking engine), so this scans the rendered page for
currency-formatted prices near booking-related content instead of relying on
fixed selectors.

Used for both the target property (name known) and competitors (name derived
from the page itself, since competitors are entered as URLs only).
"""

import asyncio
import re
from datetime import datetime
from urllib.parse import urlparse
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base_scraper import BaseScraper, RawRate

PRICE_PATTERN = re.compile(r"(?:R|ZAR)\s?([\d][\d,\s]{2,7}(?:\.\d{2})?)\b", re.IGNORECASE)
BOOKING_KEYWORDS = re.compile(
    r"book|rate|room|night|stay|availab|reserv|price|check.?in|tariff",
    re.IGNORECASE,
)


class DirectScraper(BaseScraper):
    CHANNEL = "direct"

    async def run(self, url: str, property_name: str = None) -> list[dict]:
        """Overrides BaseScraper.run() to carry an optional property_name through —
        known for the target property, derived from the page for competitors."""
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                results = await self.scrape(url, property_name)
                self.logger.info(f"[{self.CHANNEL}] scraped {len(results)} rates for '{url}'")
                return [r.to_dict() for r in results]
            except Exception as e:
                self.logger.warning(f"[{self.CHANNEL}] attempt {attempt + 1} failed: {e}")
                if attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.RETRY_DELAY)
                else:
                    self.logger.error(f"[{self.CHANNEL}] all retries exhausted for '{url}'")
                    return []

    async def scrape(self, url: str, property_name: str = None) -> list[RawRate]:
        ci, co = self._build_dates()
        scraped_at = datetime.utcnow().isoformat()

        if not url or not url.strip():
            return []
        if not url.startswith("http"):
            url = "https://" + url

        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            page = await self._new_stealth_page(browser)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(3000)

                # Dismiss common cookie/consent banners — best effort, ignore failures.
                for sel in ['[id*="cookie" i] button', '[class*="cookie" i] button', 'button[aria-label*="accept" i]']:
                    try:
                        await page.click(sel, timeout=1500)
                    except Exception:
                        pass

                # Look for an explicit "book now" / "check rates" entry point and follow
                # it once — many hotel sites hide pricing behind a booking-widget click.
                for sel in ['a:has-text("Book Now")', 'a:has-text("Check Availability")',
                            'button:has-text("Book Now")', 'button:has-text("Check Rates")']:
                    try:
                        await page.click(sel, timeout=2000)
                        await page.wait_for_timeout(3000)
                        break
                    except Exception:
                        continue

                resolved_name = property_name or await self._derive_property_name(page, url)
                rates = await self._extract_heuristic_prices(page, resolved_name, ci, co, url, scraped_at)
                await browser.close()
                return rates
            except PlaywrightTimeout:
                self.logger.error(f"[direct] timeout loading '{url}'")
                await browser.close()
                return []
            except Exception as e:
                self.logger.error(f"[direct] unexpected error: {e}")
                await browser.close()
                return []

    async def _derive_property_name(self, page, url: str) -> str:
        """No name is supplied for competitor URLs — best-effort label from the
        page's own <title>, falling back to the domain name."""
        try:
            title = (await page.title()).strip()
            for sep in [" | ", " – ", " — ", " - ", " :: "]:
                if sep in title:
                    title = title.split(sep)[0].strip()
                    break
            if 2 <= len(title) <= 80:
                return title
        except Exception:
            pass
        host = (urlparse(url).netloc or url).replace("www.", "").split(".")[0]
        return host.replace("-", " ").title() or "Competitor (direct site)"

    async def _extract_heuristic_prices(self, page, property_name, ci, co, source_url, scraped_at) -> list[RawRate]:
        """Scan the page for currency-formatted numbers near booking-related text.
        Low-confidence by nature — every hotel's site is structured differently."""
        rates = []
        body_text = await page.inner_text("body")
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]

        seen_prices = set()
        for i, line in enumerate(lines):
            for match in PRICE_PATTERN.finditer(line):
                raw_num = match.group(1).replace(",", "").replace(" ", "")
                try:
                    price_zar = float(raw_num)
                except ValueError:
                    continue
                if price_zar < 200 or price_zar > 50000:
                    continue  # implausible for a per-night hotel rate — likely noise
                if price_zar in seen_prices:
                    continue

                context = " ".join(lines[max(0, i - 2):i + 1])
                if not BOOKING_KEYWORDS.search(context):
                    continue  # price-looking number with no booking context nearby

                seen_prices.add(price_zar)
                rates.append(RawRate(
                    channel=self.CHANNEL,
                    property_name=property_name,
                    check_in=ci,
                    check_out=co,
                    room_label=context[:120],
                    rate_label="Direct website (unverified)",
                    price_zar=price_zar,
                    price_raw=match.group(0),
                    currency_raw="ZAR",
                    availability=True,
                    scraped_at=scraped_at,
                    source_url=source_url,
                    extras={"heuristic": True, "confidence": "LOW"},
                ))
                if len(rates) >= 10:
                    return rates

        return rates
