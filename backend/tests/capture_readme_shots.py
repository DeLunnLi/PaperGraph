"""Capture README screenshots of every main page at a true desktop size.

Renders at 1440x900 @2x via Playwright/Chromium. Logs in through the real login
form (so the router guard and axios interceptor behave normally), then visits
each route and waits for a route-specific "content is ready" signal before
shooting — a fixed sleep is flaky because several pages stream LLM output.

Run:
    conda run -n papergraph python backend/tests/capture_readme_shots.py
Env:
    PG_USER / PG_PASS   demo credentials (default: demo / demo1234)
    OUT_DIR             output directory (default: /tmp/pg_shots)
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from playwright.async_api import Page, async_playwright

BASE = os.environ.get("PG_BASE", "http://127.0.0.1:5173")
USER = os.environ.get("PG_USER", "demo")
PASS = os.environ.get("PG_PASS", "demo1234")
OUT = Path(os.environ.get("OUT_DIR", "/tmp/pg_shots"))
VIEWPORT = {"width": 1440, "height": 900}


async def login(page: Page) -> None:
    await page.goto(f"{BASE}/login", wait_until="networkidle")
    await page.get_by_placeholder("用户名").fill(USER)
    await page.get_by_placeholder("密码").fill(PASS)
    await page.get_by_role("button", name="登 录").click()
    await page.wait_for_url(lambda u: "/login" not in u, timeout=30_000)
    await page.wait_for_load_state("networkidle")


async def scroll_to_top(page: Page) -> None:
    """Chat-style pages auto-scroll to the newest message; rewind so the shot
    shows the query + first results instead of landing mid-list."""
    await page.evaluate(
        """() => {
            window.scrollTo(0, 0);
            document.querySelectorAll('*').forEach((el) => {
                if (el.scrollHeight > el.clientHeight + 40) el.scrollTop = 0;
            });
        }"""
    )
    await page.wait_for_timeout(900)


async def shoot(page: Page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    # Viewport-only (not full_page): full_page on these tall flex layouts can
    # produce a stretched/short strip in Chromium.
    await page.screenshot(path=str(path), full_page=False)
    kb = path.stat().st_size / 1024
    print(f"  saved {path}  ({kb:.0f} KB)")


async def goto(page: Page, route: str, wait_until: str = "networkidle") -> None:
    # Streaming pages (reader) never go idle, so callers can relax wait_until.
    await page.goto(f"{BASE}{route}", wait_until=wait_until, timeout=60_000)
    # Let the sidebar/route transition settle.
    await page.wait_for_timeout(1200)


async def wait_for_any_text(page: Page, texts: list[str], timeout: int = 60_000) -> bool:
    """Resolve as soon as any of `texts` appears; tolerate none appearing."""
    try:
        await page.wait_for_function(
            "ts => ts.some(t => document.body.innerText.includes(t))",
            arg=texts,
            timeout=timeout,
        )
        return True
    except Exception:
        print(f"    ! none of {texts} appeared within {timeout}ms")
        return False


async def first_library_paper_id(page: Page) -> int | None:
    """Ask the library API (from inside the page, reusing its token) for a paper
    id, preferring one that already has a PDF so the reader has something to render."""
    return await page.evaluate(
        """async () => {
            const token = localStorage.getItem('pg_token');
            const r = await fetch('/api/papers/library', {
                headers: { Authorization: 'Bearer ' + token },
            });
            if (!r.ok) return null;
            const data = await r.json();
            const items = Array.isArray(data) ? data : (data.papers || []);
            if (!items.length) return null;
            const withPdf = items.find((p) => p.local_pdf_path || p.pdf_url);
            return (withPdf || items[0]).id ?? null;
        }"""
    )


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport=VIEWPORT, device_scale_factor=2)
        page = await ctx.new_page()
        page.on("pageerror", lambda e: print(f"    [pageerror] {e}"))

        print(f"login as {USER} ...")
        await login(page)

        # 1. Search — replay a query so the page shows real fused results.
        print("search ...")
        await goto(page, "/search")
        box = page.get_by_placeholder("输入关键词、研究方向或论文标题，即可检索相关论文")
        if await box.count():
            await box.fill("检索增强生成 RAG 的优化策略")
            await page.get_by_role("button", name="搜索").first.click()
            await wait_for_any_text(page, ["为你找到", "找到 "], timeout=180_000)
            await page.wait_for_timeout(2500)
            await scroll_to_top(page)
        await shoot(page, "search-agent-results")

        # 2. Library
        print("library ...")
        await goto(page, "/library")
        await wait_for_any_text(page, ["共 ", "篇论文"], timeout=30_000)
        await page.wait_for_timeout(1500)
        await shoot(page, "library-management")

        # 3. Daily recommendations (may fetch arXiv on first load)
        print("daily ...")
        await goto(page, "/daily")
        await wait_for_any_text(page, ["推荐", "arXiv", "今日"], timeout=180_000)
        await page.wait_for_timeout(3000)
        await shoot(page, "daily-recommendations")

        # 4. Knowledge graph (d3 force layout needs time to settle)
        print("graph ...")
        await goto(page, "/graph")
        await page.wait_for_timeout(6000)
        await shoot(page, "knowledge-graph")

        # 5. Knowledge base
        print("knowledge base ...")
        await goto(page, "/knowledge")
        await page.wait_for_timeout(3000)
        await shoot(page, "knowledge-base")

        # 6. Reader. Library.vue's 阅读 button uses window.open() to a new tab,
        # so clicking it never changes the current page's URL. Navigate directly
        # to the reader route for the first paper instead.
        print("reader ...")
        paper_id = await first_library_paper_id(page)
        if paper_id is None:
            print("    ! could not resolve a paper id; skipping reader")
        else:
            print(f"    opening paper {paper_id}")
            await goto(page, f"/library/read/{paper_id}", wait_until="domcontentloaded")
            # PDF fetch + parse + opening summary are LLM-backed; wait generously.
            await wait_for_any_text(page, ["摘要", "总结", "提问", "本文"], timeout=300_000)
            await page.wait_for_timeout(8000)
            await shoot(page, "paper-reader-assistant")

        await browser.close()
        print(f"\ndone -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
