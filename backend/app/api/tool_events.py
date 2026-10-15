
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

@dataclass
class ToolEvent:
    ts_ms: int
    type: str
    payload: Dict[str, Any]

class ToolCallTracker:

    def __init__(self, sink: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self._sink = sink

    def emit(self, type: str, payload: Dict[str, Any]) -> None:
        if self._sink:
            ev = ToolEvent(ts_ms=int(time.time() * 1000), type=type, payload=dict(payload or {}))
            self._sink(self.to_wire(ev))

    def to_wire(self, ev: ToolEvent) -> Dict[str, Any]:
        return {"type": ev.type, "ts_ms": ev.ts_ms, **(ev.payload or {})}

def sse_pack(event: Dict[str, Any]) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"
