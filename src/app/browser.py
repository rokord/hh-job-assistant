from __future__ import annotations

import logging
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright


logger = logging.getLogger(__name__)


class BrowserSession:
    def __init__(self, user_data_dir: Path, headless: bool) -> None:
        self.user_data_dir = user_data_dir
        self.headless = headless
        self._playwright = None
        self._context: BrowserContext | None = None

    def __enter__(self) -> tuple[BrowserContext, Page]:
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Starting browser session")
        logger.info("Browser profile dir: %s", self.user_data_dir)

        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=self.headless,
            viewport={"width": 1400, "height": 900},
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )

        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        return self._context, page

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._context is not None:
            self._context.close()
        if self._playwright is not None:
            self._playwright.stop()
        logger.info("Browser session closed")
