"""Trace 文件 retention —— 防止 hello-agents TraceLogger 无界增长。

hello-agents 的 TraceLogger 每次 SimpleAgent.run() 写一对 trace-{session_id}.jsonl/.html，
且**无任何清理/轮转机制**（源码只有 __init__/log_event/finalize）。生产跑数月会累积
GB 级垃圾。本模块提供：

- ``cleanup_traces``：按 age（超 N 天删）+ count（超 N 个保留最新）双策略清理，按
  session_id 成对删除 jsonl+html，避免孤儿文件。
- ``spawn_trace_cleanup_task``：后台 asyncio 任务，按间隔周期清理；镜像
  daily_auto_refresh 的 spawn 模式。

清理只删 trace-*.jsonl / trace-*.html，不碰目录里其他文件。失败容错（单文件删除
失败不中断），返回统计 dict 供日志/监控。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ...settings import get_settings

logger = logging.getLogger(__name__)

# hello-agents TraceLogger 写的文件名：trace-{session_id}.jsonl/.html，
# session_id 形如 s-20260713-153017-fc1f（"s-" + 8位日期 + "-" + 6位时间 + "-" + 4位hex）。
# 用正则严格匹配，避免误删用户手动放的 trace-backup-*.jsonl 等非 trace 文件。
_TRACE_SESSION_RE = re.compile(r"^trace-(s-\d{8}-\d{6}-[0-9a-f]+)\.(jsonl|html)$")


def _collect_sessions(trace_dir: Path) -> dict[str, dict[str, Path]]:
    """返回 {session_id: {'jsonl': Path|None, 'html': Path|None}}。"""
    sessions: dict[str, dict[str, Path]] = {}
    if not trace_dir.exists():
        return sessions
    for path in trace_dir.iterdir():
        if not path.is_file():
            continue
        m = _TRACE_SESSION_RE.match(path.name)
        if m is None:
            continue
        sid = m.group(1)
        slot = sessions.setdefault(sid, {"jsonl": None, "html": None})
        if m.group(2) == "jsonl":
            slot["jsonl"] = path
        else:
            slot["html"] = path
    return sessions


def _session_mtime(files: dict[str, Path]) -> float:
    """会话 mtime = jsonl/html 中较新者（两者同时写，近似相等）。"""
    mt = 0.0
    for p in files.values():
        if p is not None and p.exists():
            try:
                mt = max(mt, p.stat().st_mtime)
            except OSError:
                continue
    return mt


def _delete_pair(files: dict[str, Path]) -> int:
    """删除 jsonl+html 一对，返回实际删除文件数。"""
    n = 0
    for p in files.values():
        if p is None:
            continue
        try:
            p.unlink(missing_ok=True)
            n += 1
        except OSError as exc:
            logger.debug("trace cleanup: 删除 %s 失败: %s", p, exc, exc_info=False)
    return n


def cleanup_traces(
    *,
    trace_dir: Path | str | None = None,
    max_age_days: int | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
    """清理 trace 文件。

    Args:
        trace_dir: trace 目录；None 则从 settings 读。
        max_age_days: 超过 N 天的会话删除；None 则从 settings 读。
        max_files: 保留最新 N 个会话，其余删除；None 则从 settings 读。

    Returns:
        {"sessions_before", "sessions_after", "deleted_sessions", "deleted_files"}
    """
    s = get_settings()
    trace_dir = Path(trace_dir) if trace_dir is not None else _default_trace_dir()
    max_age_days = int(max_age_days if max_age_days is not None else getattr(s, "trace_retention_days", 7))
    max_files = int(max_files if max_files is not None else getattr(s, "trace_max_files", 1000))

    sessions = _collect_sessions(trace_dir)
    before = len(sessions)
    if not sessions:
        return {"sessions_before": 0, "sessions_after": 0, "deleted_sessions": 0, "deleted_files": 0}

    now = time.time()
    age_cutoff = now - max_age_days * 86400

    # 策略 1：按 age 删除
    to_delete: set[str] = set()
    for sid, files in sessions.items():
        if _session_mtime(files) < age_cutoff:
            to_delete.add(sid)

    # 策略 2：按 count 截断（在 age 删除后的剩余里，保留最新 max_files 个）
    remaining = [(sid, _session_mtime(files)) for sid, files in sessions.items() if sid not in to_delete]
    remaining.sort(key=lambda x: x[1], reverse=True)  # 新→旧
    if len(remaining) > max_files:
        for sid, _mt in remaining[max_files:]:
            to_delete.add(sid)

    deleted_files = 0
    for sid in to_delete:
        deleted_files += _delete_pair(sessions[sid])
        del sessions[sid]

    stats = {
        "sessions_before": before,
        "sessions_after": len(sessions),
        "deleted_sessions": before - len(sessions),
        "deleted_files": deleted_files,
    }
    if stats["deleted_sessions"]:
        logger.info(
            "trace cleanup: 删除 %d 个会话（%d 文件），剩 %d 个",
            stats["deleted_sessions"], deleted_files, stats["sessions_after"],
        )
    return stats


def _default_trace_dir() -> Path:
    s = get_settings()
    return Path(s.data_dir) / "memory" / "traces"


async def trace_cleanup_loop(app: FastAPI) -> None:
    """后台周期清理 loop。间隔从 settings.trace_cleanup_interval_sec 读；<=0 则只跑一次。"""
    s = get_settings()
    interval = int(getattr(s, "trace_cleanup_interval_sec", 3600) or 0)
    try:
        # 启动时先清理一次（baseline）
        cleanup_traces()
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                cleanup_traces()
            except Exception:
                logger.debug("trace cleanup loop 周期清理失败", exc_info=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("trace cleanup loop 异常退出", exc_info=True)


def spawn_trace_cleanup_task(app: FastAPI) -> asyncio.Task | None:
    """启动 trace 清理后台任务；返回 Task 供 shutdown 时 cancel。"""
    try:
        return asyncio.create_task(trace_cleanup_loop(app), name="papergraph_trace_cleanup")
    except RuntimeError:
        # 无运行中的 event loop（罕见）—— 启动时已同步清理过，跳过后台任务
        logger.debug("trace cleanup: 无 event loop，跳过后台任务")
        return None
