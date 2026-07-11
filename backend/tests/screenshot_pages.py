"""Render PaperGraph pages and save screenshots for visual review.

Run: conda run -n papergraph python tests/screenshot_pages.py
Outputs PNGs to /tmp/papergraph_shots/
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:5173"
OUT = Path("/tmp/papergraph_shots")
OUT.mkdir(parents=True, exist_ok=True)


async def shot(page, url: str, name: str, *, wait_ms: int = 1500):
    await page.goto(url, wait_until="networkidle")
    await page.wait_for_timeout(wait_ms)
    path = OUT / f"{name}.png"
    # Use viewport screenshot (not full_page) — full_page triggers a Chromium
    # paging bug that returns a 16px strip on tall pages.
    await page.screenshot(path=str(path), full_page=False)
    print(f"  saved {path}")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 820}, device_scale_factor=1)
        page = await ctx.new_page()
        # Capture console errors
        page.on("console", lambda msg: print(f"  [console.{msg.type}] {msg.text}") if msg.type == "error" else None)
        page.on("pageerror", lambda err: print(f"  [pageerror] {err}"))

        print("screenshots -> /tmp/papergraph_shots/")
        # Search landing (default route)
        await shot(page, f"{BASE}/", "01_search_landing", wait_ms=2500)
        # Daily
        await shot(page, f"{BASE}/daily", "02_daily", wait_ms=2500)
        # Library
        await shot(page, f"{BASE}/library", "03_library", wait_ms=2500)
        # Knowledge graph
        await shot(page, f"{BASE}/graph", "04_knowledge_graph", wait_ms=3000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
