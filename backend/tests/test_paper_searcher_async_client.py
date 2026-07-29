from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.search.paper_searcher import PaperSearcher


def test_async_client_respects_httpx_trust_env_flag():
    async def _run() -> None:
        searcher = PaperSearcher(httpx_trust_env=False)
        client = await searcher._ensure_async_client()
        try:
            assert client._trust_env is False
        finally:
            await searcher.aclose()

        searcher2 = PaperSearcher(httpx_trust_env=True)
        client2 = await searcher2._ensure_async_client()
        try:
            assert client2._trust_env is True
        finally:
            await searcher2.aclose()

    asyncio.run(_run())
