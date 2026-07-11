"""Per-request reader context — isolates mutable state across concurrent
`paper_reader_reply` calls.

`PaperAnalysisAgent` is a process-wide singleton shared by all paper-reader
requests (which run on threadpool workers). Without isolation, the singleton's
`_reader_snap` / `_reader_last_user_message` / `_reader_lookup_buffer` and the
shared `SimpleAgent._history` cross-contaminate between concurrent requests.

`ReaderCtx` holds the per-request copies. Lifetime = one `paper_reader_reply`
call: created on entry, dropped on return. `_reader_reco_ref_offset` (per-paper
recommendation pagination) intentionally stays on the singleton — it must
persist across requests so recommendations don't repeat.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, List, Tuple


@dataclass
class ReaderCtx:
    snap: dict[str, Any]
    user_message: str = ""
    lookup_buffer: List[Tuple[Any, str]] = field(default_factory=list)
    lookup_lock: threading.Lock = field(default_factory=threading.Lock)
