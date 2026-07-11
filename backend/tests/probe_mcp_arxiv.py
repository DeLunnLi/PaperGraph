"""Probe: spawn arxiv-mcp-server via stdio, call search_papers, capture schema.

Run: conda run -n papergraph python tests/probe_mcp_arxiv.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


async def main() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Find the arxiv-mcp-server console script in the active env's bin.
    env_bin = os.path.join(os.path.dirname(sys.executable), "arxiv-mcp-server")
    if not os.path.exists(env_bin):
        # fall back to PATH
        env_bin = "arxiv-mcp-server"
    print(f"[probe] server binary: {env_bin}")

    storage = BACKEND_ROOT / "data" / "mcp_arxiv_storage"
    storage.mkdir(parents=True, exist_ok=True)

    params = StdioServerParameters(
        command=env_bin,
        args=["--storage-path", str(storage)],
    )

    print("[probe] spawning stdio server...")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            print("[probe] initializing session...")
            await session.initialize()
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"[probe] available tools: {tool_names}")

            print("[probe] calling search_papers(query='Kolmogorov-Arnold Networks', max_results=3)...")
            result = await session.call_tool(
                "search_papers",
                {"query": "Kolmogorov-Arnold Networks", "max_results": 3},
            )
            print(f"[probe] result is_error: {result.isError}")
            print(f"[probe] content blocks: {len(result.content)}")
            for block in result.content:
                btype = getattr(block, "type", "?")
                print(f"[probe]   block type={btype}")
                if btype == "text":
                    text = getattr(block, "text", "")
                    print(f"[probe]   text len={len(text)}")
                    # Try to parse as JSON to learn the schema
                    try:
                        data = json.loads(text)
                        print(f"[probe]   parsed JSON type={type(data).__name__}")
                        if isinstance(data, list) and data:
                            print(f"[probe]   first paper keys: {list(data[0].keys())}")
                            print(f"[probe]   first paper sample:")
                            sample = data[0]
                            for k, v in sample.items():
                                vs = str(v)
                                print(f"[probe]     {k}: {vs[:120]}")
                        elif isinstance(data, dict):
                            print(f"[probe]   dict keys: {list(data.keys())}")
                            # maybe {"papers": [...]} or {"total_results": N, "papers": [...]}
                            for k, v in data.items():
                                print(f"[probe]     {k}: {type(v).__name__} {str(v)[:120]}")
                    except json.JSONDecodeError:
                        print(f"[probe]   not JSON, raw head: {text[:300]}")
            print("[probe] done.")


if __name__ == "__main__":
    asyncio.run(main())
