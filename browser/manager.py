from __future__ import annotations

import asyncio
import subprocess
import logging
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config import CHROME_PATH, CDP_PORT, CDP_URL, TIKTOK_CREATIVE_URL, BASE_DIR, DOWNLOADS_DIR

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._chrome_process = None

    async def launch_chrome(self):
        user_data_dir = BASE_DIR / "chrome_profile"
        self._chrome_process = subprocess.Popen(
            [
                CHROME_PATH,
                f"--remote-debugging-port={CDP_PORT}",
                f"--user-data-dir={user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        await asyncio.sleep(3)
        logger.info("Chrome launched with CDP on port %d, profile=%s", CDP_PORT, user_data_dir)

    async def connect(self) -> BrowserContext:
        if self._context:
            return self._context
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(CDP_URL)
        except Exception:
            logger.info("Chrome not running, launching...")
            await self.launch_chrome()
            self._browser = await self._playwright.chromium.connect_over_cdp(CDP_URL)
        self._context = self._browser.contexts[0]
        return self._context

    async def new_page(self, url: str | None = None) -> Page:
        ctx = await self.connect()
        page = await ctx.new_page()
        if url:
            await page.goto(url, wait_until="domcontentloaded")
        return page

    async def open_tiktok(self) -> Page:
        """Find existing TikTok Creative Studio tab, or open a new one."""
        ctx = await self.connect()
        for page in ctx.pages:
            if "ads.tiktok.com/creative" in page.url:
                logger.info("Reusing existing TikTok tab: %s", page.url)
                await self._setup_download_behavior(page)
                return page
        logger.info("No TikTok tab found, opening new one")
        page = await self.new_page(TIKTOK_CREATIVE_URL)
        await self._setup_download_behavior(page)
        return page

    async def _setup_download_behavior(self, page: Page):
        """Configure Chrome to download files to our downloads directory via CDP."""
        try:
            DOWNLOADS_DIR.mkdir(exist_ok=True)
            cdp = await page.context.new_cdp_session(page)
            await cdp.send("Page.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": str(DOWNLOADS_DIR.resolve()),
            })
            logger.info("CDP download path set to %s", DOWNLOADS_DIR.resolve())
        except Exception as e:
            logger.warning("Failed to set CDP download behavior: %s", e)

    async def close(self):
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


browser_manager = BrowserManager()
