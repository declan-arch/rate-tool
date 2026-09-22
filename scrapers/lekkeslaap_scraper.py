"""
LekkeSlaap.co.za scraper — South African OTA.
"""

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base_scraper import BaseScraper, RawRate


class LekkeSlaapScraper(BaseScraper):
    CHANNEL = "lekkeslaap"
    BASE_URL = "https://www.lekkeslaap.co.za"

    async def scrape(self, search_term: str) -> list[RawRate]:
        ci, co = self._build_dates()
        adults = self.scrape_cfg["adults"]
        # LekkeSlaap date format: DD-MM-YYYY
        ci_ls = datetime.strptime(ci, "%Y-%m-%d").strftime("%d-%m-%Y")
        co_ls = datetime.strptime(co, "%Y-%m-%d").strftime("%d-%m-%Y")

        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            page = await self._new_stealth_page(browser)

            try:
                search_url = (
                    f"{self.BASE_URL}/akkommodasie?"
                    f"search={quote_plus(search_term)}"
                    f"&check_in={ci_ls}&check_out={co_ls}&adults={adults}&children=0"
                )
                self.logger.info(f"[lekkeslaap] searching: {search_url}")
                await page.goto(search_url, wait_until="domcontentloaded",
                                timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(3000)

                # Find first result
                property_link = None
                property_name = search_term
                result_selectors = [
                    '.search-result-card a',
                    '.property-card a',
                    '.listing-card a',
                    'a.property-link',
                ]
                for sel in result_selectors:
                    el = await page.query_selector(sel)
                    if el:
                        property_link = await el.get_attribute("href")
                        name_el = await page.query_selector('.property-name, .listing-title, h2.card-title')
                        if name_el:
                            property_name = (await name_el.inner_text()).strip()
                        break

                if not property_link:
                    # Try direct search by name
                    self.logger.warning(f"[lekkeslaap] no results for '{search_term}' via search, trying direct URL")
                    await browser.close()
                    return []

                if not property_link.startswith("http"):
                    property_link = self.BASE_URL + property_link

                await page.goto(property_link, wait_until="domcontentloaded",
                                timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(3000)

                rates = await self._extract_rates(page, property_name, ci, co, property_link)
                await browser.close()
                return rates

            except PlaywrightTimeout:
                self.logger.error(f"[lekkeslaap] timeout for '{search_term}'")
                await browser.close()
                return []
            except Exception as e:
                self.logger.error(f"[lekkeslaap] error: {e}")
                await browser.close()
                return []

    async def _extract_rates(self, page, property_name, ci, co, source_url) -> list[RawRate]:
        rates = []
        scraped_at = datetime.utcnow().isoformat()

        # LekkeSlaap typically shows unit/room types with nightly prices
        unit_selectors = [
            '.accommodation-type',
            '.unit-card',
            '.room-option',
            '.accommodation-option',
        ]

        units = []
        for sel in unit_selectors:
            units = await page.query_selector_all(sel)
            if units:
                break

        if not units:
            # Grab any visible price block
            price_els = await page.query_selector_all('.price, .nightly-rate, [class*="price"]')
            for el in price_els[:6]:
                price_raw = (await el.inner_text()).strip()
                nums = re.findall(r'[\d\s]+', price_raw)
                cleaned = ''.join(nums).replace(' ', '')
                if cleaned and len(cleaned) >= 3:
                    try:
                        price_zar = float(cleaned)
                        rates.append(RawRate(
                            channel=self.CHANNEL,
                            property_name=property_name,
                            check_in=ci, check_out=co,
                            room_label="Unknown Unit",
                            rate_label="Nightly Rate",
                            price_zar=price_zar,
                            price_raw=price_raw,
                            currency_raw="ZAR",
                            availability=True,
                            scraped_at=scraped_at,
                            source_url=source_url,
                            extras={"fallback": True}
                        ))
                    except ValueError:
                        pass
            return rates

        for unit in units:
            try:
                room_label = ""
                for sel in ['.unit-name', '.room-name', 'h3', 'h4', '.title']:
                    el = await unit.query_selector(sel)
                    if el:
                        room_label = (await el.inner_text()).strip()
                        break

                price_raw = ""
                price_zar = None
                for sel in ['.price', '.nightly-rate', '.rate', '[class*="price"]']:
                    el = await unit.query_selector(sel)
                    if el:
                        price_raw = (await el.inner_text()).strip()
                        nums = re.findall(r'[\d]+', price_raw.replace(',', '').replace('\xa0', '').replace(' ', ''))
                        if nums:
                            price_zar = float(''.join(nums[:1]))
                        break

                rates.append(RawRate(
                    channel=self.CHANNEL,
                    property_name=property_name,
                    check_in=ci, check_out=co,
                    room_label=room_label or "Unit",
                    rate_label="Room Only",
                    price_zar=price_zar,
                    price_raw=price_raw,
                    currency_raw="ZAR",
                    availability=True,
                    scraped_at=scraped_at,
                    source_url=source_url,
                    extras={}
                ))
            except Exception as e:
                self.logger.debug(f"[lekkeslaap] row error: {e}")
                continue

        return rates
