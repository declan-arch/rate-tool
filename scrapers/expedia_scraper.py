"""
Expedia scraper (expedia.co.za) for SA properties.
"""

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base_scraper import BaseScraper, RawRate


class ExpediaScraper(BaseScraper):
    CHANNEL = "expedia"
    BASE_URL = "https://www.expedia.co.za"

    async def scrape(self, search_term: str) -> list[RawRate]:
        ci, co = self._build_dates()
        adults = self.scrape_cfg["adults"]

        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            page = await self._new_stealth_page(browser)

            try:
                search_url = (
                    f"{self.BASE_URL}/Hotel-Search?"
                    f"destination={quote_plus(search_term)}"
                    f"&startDate={ci}&endDate={co}"
                    f"&adults={adults}&rooms=1"
                )
                self.logger.info(f"[expedia] searching: {search_url}")
                await page.goto(search_url, wait_until="domcontentloaded",
                                timeout=max(self.scrape_cfg["timeout_ms"], 60000))
                await page.wait_for_timeout(8000)

                # Dismiss any modal/popup
                for close_sel in ['[data-stid="dialog-close"]', 'button[aria-label="Close"]', '.uitk-dialog-close']:
                    try:
                        await page.click(close_sel, timeout=2000)
                    except Exception:
                        pass

                # Find first property card
                property_link = None
                property_name = search_term
                for sel in [
                    '[data-stid="open-hotel-information"] a',
                    '[data-testid="property-card"] a',
                    '.uitk-card a',
                    'a[data-stid*="hotel"]',
                ]:
                    el = await page.query_selector(sel)
                    if el:
                        property_link = await el.get_attribute("href")
                        name_el = await page.query_selector('[data-stid="content-hotel-title"]')
                        if name_el:
                            property_name = (await name_el.inner_text()).strip()
                        break

                if not property_link:
                    await browser.close()
                    return []

                if not property_link.startswith("http"):
                    property_link = self.BASE_URL + property_link

                self.logger.info(f"[expedia] visiting: {property_link}")
                await page.goto(property_link, wait_until="domcontentloaded",
                                timeout=max(self.scrape_cfg["timeout_ms"], 60000))
                await page.wait_for_timeout(8000)

                rates = await self._extract_rates(page, property_name, ci, co, property_link)
                await browser.close()
                return rates

            except Exception as e:
                self.logger.error(f"[expedia] error: {e}")
                await browser.close()
                return []

    async def _extract_rates(self, page, property_name, ci, co, source_url) -> list[RawRate]:
        rates = []
        scraped_at = datetime.utcnow().isoformat()

        room_selectors = [
            '[data-stid="section-room-list"] [data-stid="room-name"]',
            '.room-unit-title',
            '[data-testid="room-type"]',
        ]

        room_els = []
        for sel in room_selectors:
            room_els = await page.query_selector_all(sel)
            if room_els:
                break

        price_selectors = [
            '[data-stid*="price-lockup"]',
            '.price-summary',
            '[data-testid*="price"]',
            '.uitk-price-lockup',
        ]

        if room_els:
            for room_el in room_els[:8]:
                try:
                    room_label = (await room_el.inner_text()).strip()

                    # Find associated price in parent container
                    parent = await room_el.evaluate_handle("el => el.closest('[data-stid], .room-card, .room-container')")
                    price_raw = ""
                    price_zar = None

                    for sel in price_selectors:
                        price_el = await parent.query_selector(sel) if parent else None
                        if price_el:
                            price_raw = (await price_el.inner_text()).strip()
                            nums = re.findall(r'[\d,]+', price_raw.replace('\xa0', '').replace(' ', ''))
                            if nums:
                                price_zar = float(nums[0].replace(',', ''))
                            break

                    rates.append(RawRate(
                        channel=self.CHANNEL,
                        property_name=property_name,
                        check_in=ci, check_out=co,
                        room_label=room_label,
                        rate_label="",
                        price_zar=price_zar,
                        price_raw=price_raw,
                        currency_raw="ZAR",
                        availability=price_zar is not None,
                        scraped_at=scraped_at,
                        source_url=source_url,
                        extras={}
                    ))
                except Exception as e:
                    self.logger.debug(f"[expedia] room parse error: {e}")
        else:
            # Fallback: grab any prices on page
            for sel in price_selectors:
                els = await page.query_selector_all(sel)
                for el in els[:6]:
                    price_raw = (await el.inner_text()).strip()
                    nums = re.findall(r'[\d,]+', price_raw.replace('\xa0', '').replace(' ', ''))
                    if nums and len(nums[0]) >= 3:
                        try:
                            rates.append(RawRate(
                                channel=self.CHANNEL,
                                property_name=property_name,
                                check_in=ci, check_out=co,
                                room_label="Unknown Room",
                                rate_label="",
                                price_zar=float(nums[0].replace(',', '')),
                                price_raw=price_raw,
                                currency_raw="ZAR",
                                availability=True,
                                scraped_at=scraped_at,
                                source_url=source_url,
                                extras={"fallback": True}
                            ))
                        except ValueError:
                            pass
                if rates:
                    break

        return rates
