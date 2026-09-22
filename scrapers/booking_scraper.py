"""
Booking.com scraper for The Tyrwhitt and competitor properties.
Navigates search results → property page → room list.
"""

import asyncio
import re
from datetime import datetime
from urllib.parse import quote_plus
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from .base_scraper import BaseScraper, RawRate


class BookingScraper(BaseScraper):
    CHANNEL = "booking_com"
    BASE_URL = "https://www.booking.com"

    async def scrape(self, search_term: str) -> list[RawRate]:
        ci, co = self._build_dates()
        adults = self.scrape_cfg["adults"]

        async with async_playwright() as pw:
            browser = await self._launch_browser(pw)
            page = await self._new_stealth_page(browser)

            try:
                # Step 1: Search for the property
                search_url = (
                    f"{self.BASE_URL}/searchresults.html?"
                    f"ss={quote_plus(search_term)}"
                    f"&checkin={ci}&checkout={co}"
                    f"&group_adults={adults}&no_rooms=1&group_children=0"
                    f"&selected_currency=ZAR"
                )
                self.logger.info(f"[booking.com] searching: {search_url}")
                await page.goto(search_url, wait_until="domcontentloaded",
                                timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(3000)

                # Dismiss cookie banner if present
                try:
                    await page.click('[id="onetrust-accept-btn-handler"]', timeout=3000)
                except Exception:
                    pass

                # Step 2: Find first property result and click
                property_link = None
                result_selectors = [
                    '[data-testid="property-card"] a[data-testid="title-link"]',
                    '.sr_property_block a.hotel_name_link',
                    '[data-testid="title-link"]',
                ]
                for sel in result_selectors:
                    try:
                        el = await page.query_selector(sel)
                        if el:
                            property_link = await el.get_attribute("href")
                            property_name_el = await page.query_selector(
                                '[data-testid="title"]'
                            )
                            property_name = (
                                await property_name_el.inner_text() if property_name_el else search_term
                            )
                            break
                    except Exception:
                        continue

                if not property_link:
                    self.logger.warning(f"[booking.com] no results for '{search_term}'")
                    await browser.close()
                    return []

                # Step 3: Navigate to property page
                if not property_link.startswith("http"):
                    property_link = self.BASE_URL + property_link

                self.logger.info(f"[booking.com] visiting property: {property_link}")
                await page.goto(property_link, wait_until="domcontentloaded",
                                timeout=self.scrape_cfg["timeout_ms"])
                await page.wait_for_timeout(3000)

                # Re-confirm property name from page
                try:
                    name_el = await page.query_selector('h2[data-capla-component*="PropertyHeader"], .hp__hotel-name, h1.pp-header__title')
                    property_name = await name_el.inner_text() if name_el else search_term
                    property_name = property_name.strip()
                except Exception:
                    property_name = search_term

                # Step 4: Extract room rows
                rates = await self._extract_room_rates(page, property_name, ci, co, property_link)
                await browser.close()
                return rates

            except PlaywrightTimeout:
                self.logger.error(f"[booking.com] timeout for '{search_term}'")
                await browser.close()
                return []
            except Exception as e:
                self.logger.error(f"[booking.com] unexpected error: {e}")
                await browser.close()
                return []

    async def _extract_room_rates(self, page, property_name: str, ci: str, co: str,
                                   source_url: str) -> list[RawRate]:
        rates = []
        scraped_at = datetime.utcnow().isoformat()

        # Multiple possible room row selectors across Booking.com versions
        room_row_selectors = [
            '[data-testid="room-type-option"]',
            '.hprt-table tr.js-rt-block',
            'tr[data-block-id]',
            '.roomstable_row',
        ]

        room_rows = []
        for sel in room_row_selectors:
            rows = await page.query_selector_all(sel)
            if rows:
                room_rows = rows
                self.logger.info(f"[booking.com] found {len(rows)} room rows with selector '{sel}'")
                break

        if not room_rows:
            # Fallback: try to get any visible price
            self.logger.warning(f"[booking.com] no room rows found, trying fallback price extraction")
            rates.extend(await self._fallback_price_extract(page, property_name, ci, co, source_url, scraped_at))
            return rates

        last_room_label = ""
        for row in room_rows:
            try:
                # Room type label
                room_label = ""
                for sel in ['[data-testid="room-type"]', '.hprt-roomtype-link', '.room-info__title', 'td.hprt-roomtype-icon-link']:
                    el = await row.query_selector(sel)
                    if el:
                        room_label = (await el.inner_text()).strip()
                        break

                # Variant rows carry no room name — inherit the previous row's
                if room_label:
                    last_room_label = room_label
                else:
                    room_label = last_room_label

                # Rate plan / meal plan label — Booking.com exposes this as a list of
                # condition items (breakfast, cancellation, prepayment), not a single
                # description field. e.g. [data-testid="rt-rate-breakfast-included"].
                rate_label = ""
                condition_items = await row.query_selector_all('.hprt-conditions-bui li[data-testid]')
                if condition_items:
                    parts = [(await item.inner_text()).strip() for item in condition_items]
                    rate_label = " | ".join(p for p in parts if p)
                if not rate_label:
                    for sel in ['[data-testid="rate-plan-description"]', '.meal-type', '.hprt-policies-block', '.mealplan']:
                        el = await row.query_selector(sel)
                        if el:
                            rate_label = (await el.inner_text()).strip()
                            break

                # Price
                price_raw = ""
                price_zar = None
                for sel in ['[data-testid="price-and-discounted-price"]', '.bui-price-display__value',
                            '.prco-valign__middle-helper', '.hprt-price-item']:
                    el = await row.query_selector(sel)
                    if el:
                        price_raw = (await el.inner_text()).strip()
                        break

                # Parse price
                nums = re.findall(r'[\d,]+', price_raw.replace('\xa0', '').replace(' ', ''))
                if nums:
                    price_zar = float(nums[0].replace(',', ''))

                # Availability
                sold_out_el = await row.query_selector('.sold-out, [data-testid="sold-out"], .no-availability')
                availability = sold_out_el is None

                if room_label or price_raw:
                    rates.append(RawRate(
                        channel=self.CHANNEL,
                        property_name=property_name,
                        check_in=ci,
                        check_out=co,
                        room_label=room_label or "Unknown Room",
                        rate_label=rate_label or "Room Only",
                        price_zar=price_zar,
                        price_raw=price_raw,
                        currency_raw="ZAR",
                        availability=availability,
                        scraped_at=scraped_at,
                        source_url=source_url,
                        extras={}
                    ))
            except Exception as e:
                self.logger.debug(f"[booking.com] error parsing row: {e}")
                continue

        return rates

    async def _fallback_price_extract(self, page, property_name, ci, co, source_url, scraped_at) -> list[RawRate]:
        """Last resort: grab any price visible on the page."""
        rates = []
        try:
            price_els = await page.query_selector_all('[data-testid*="price"], .price, .bui-price-display__value')
            for el in price_els[:5]:
                price_raw = (await el.inner_text()).strip()
                nums = re.findall(r'[\d,]+', price_raw.replace('\xa0', '').replace(' ', ''))
                if nums and len(nums[0]) >= 3:
                    rates.append(RawRate(
                        channel=self.CHANNEL,
                        property_name=property_name,
                        check_in=ci,
                        check_out=co,
                        room_label="Unknown Room",
                        rate_label="Unknown Rate",
                        price_zar=float(nums[0].replace(',', '')),
                        price_raw=price_raw,
                        currency_raw="ZAR",
                        availability=True,
                        scraped_at=scraped_at,
                        source_url=source_url,
                        extras={"fallback": True}
                    ))
        except Exception as e:
            self.logger.error(f"[booking.com] fallback extraction failed: {e}")
        return rates
