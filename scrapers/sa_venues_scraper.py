"""
SA-Venues.com scraper.
"""

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base_scraper import BaseScraper, RawRate


class SAVenuesScraper(BaseScraper):
    CHANNEL = "sa_venues"
    BASE_URL = "https://www.sa-venues.com"

    async def scrape(self, search_term: str) -> list[RawRate]:
        ci, co = self._build_dates()

        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            page = await self._new_stealth_page(browser)

            try:
                search_url = (
                    f"{self.BASE_URL}/search/?s={quote_plus(search_term)}"
                )
                self.logger.info(f"[sa_venues] searching: {search_url}")
                await page.goto(search_url, wait_until="domcontentloaded",
                                timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(2000)

                # Find first property link
                property_link = None
                property_name = search_term
                for sel in ['.listing-title a', '.property-title a', 'h2 a', '.search-result a']:
                    el = await page.query_selector(sel)
                    if el:
                        property_link = await el.get_attribute("href")
                        property_name = (await el.inner_text()).strip()
                        break

                if not property_link:
                    await browser.close()
                    return []

                if not property_link.startswith("http"):
                    property_link = self.BASE_URL + property_link

                await page.goto(property_link, wait_until="domcontentloaded",
                                timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(2000)

                rates = await self._extract_rates(page, property_name, ci, co, property_link)
                await browser.close()
                return rates

            except Exception as e:
                self.logger.error(f"[sa_venues] error: {e}")
                await browser.close()
                return []

    async def _extract_rates(self, page, property_name, ci, co, source_url) -> list[RawRate]:
        rates = []
        scraped_at = datetime.utcnow().isoformat()

        # SA-Venues shows room types with "from R xxx per night"
        price_els = await page.query_selector_all('[class*="price"], [class*="rate"], .from-price')
        room_els = await page.query_selector_all('[class*="room"], [class*="unit"], [class*="accommodation"]')

        if not price_els and not room_els:
            # Regex scan on full page text
            content = await page.content()
            matches = re.findall(r'R\s*([\d\s,]+)\s*per\s*night', content, re.IGNORECASE)
            for m in matches[:6]:
                price_str = m.replace(',', '').replace(' ', '')
                try:
                    rates.append(RawRate(
                        channel=self.CHANNEL,
                        property_name=property_name,
                        check_in=ci, check_out=co,
                        room_label="Accommodation",
                        rate_label="Per Night",
                        price_zar=float(price_str),
                        price_raw=f"R {m} per night",
                        currency_raw="ZAR",
                        availability=True,
                        scraped_at=scraped_at,
                        source_url=source_url,
                        extras={"regex_extract": True}
                    ))
                except ValueError:
                    pass
            return rates

        for el in price_els[:8]:
            try:
                price_raw = (await el.inner_text()).strip()
                nums = re.findall(r'[\d]+', price_raw.replace(',', ''))
                if nums and int(nums[0]) > 99:
                    rates.append(RawRate(
                        channel=self.CHANNEL,
                        property_name=property_name,
                        check_in=ci, check_out=co,
                        room_label="Accommodation",
                        rate_label="Per Night",
                        price_zar=float(nums[0]),
                        price_raw=price_raw,
                        currency_raw="ZAR",
                        availability=True,
                        scraped_at=scraped_at,
                        source_url=source_url,
                        extras={}
                    ))
            except Exception:
                pass

        return rates
