
from __future__ import annotations

import logging
import os
import time
from typing import Any

from ..llm.agent_runtime import run_json_task, stateless_llm_chat
from .sqlite_document_store_compat import SQLiteDocumentStore

logger = logging.getLogger(__name__)

class MemoryStore:

    def __init__(self, db_path: str, config: Any | None = None) -> None:
        # Reader memory uses synthetic papergraph:* identities and therefore must
        # not share the application memories table, whose user_id references
        # authenticated users. Keep one sidecar DB beside the application DB.
        root = os.path.dirname(os.path.abspath(db_path))
        from .db_paths import READER_MEMORY_DB_NAME

        self.db_path = os.path.join(root, READER_MEMORY_DB_NAME)
        from .policy import get_memory_policy

        # All tunables come from the policy module (single source of truth);
        # ``config`` remains as a test override hook.
        self.config = config or get_memory_policy()
        self.store = SQLiteDocumentStore(db_path=self.db_path)

    @staticmethod
    def _user_id_for(scope: str, paper_id: int | None) -> str:
        if scope == "global":
            return "papergraph:global"
        return f"papergraph:paper:{paper_id or 0}"

    @staticmethod
    def _properties(scope: str, paper_id: int | None, kind: str, meta: dict[str, Any | None]) -> dict[str, Any]:
        p: dict[str, Any] = {}
        p["scope"] = scope
        p["paper_id"] = int(paper_id) if paper_id is not None else None
        p["kind"] = kind
        if meta:
            p.update(meta)
        return p

    @staticmethod
    def _content_of(doc: dict) -> str:
        return str(doc.get("content") or "").strip()

    @staticmethod
    def _memory_id_of(doc: dict) -> str:
        return str(doc.get("memory_id") or "").strip()

    @staticmethod
    def _ts_of(doc: dict) -> int:
        return int(doc.get("timestamp") or 0)

    @staticmethod
    def _imp_of(doc: dict, default: float = 0.0) -> float:
        try:
            return float(doc.get("importance") or default)
        except Exception:
            return default

    @staticmethod
    def _type_of(doc: dict) -> str:
        return str(doc.get("memory_type") or "").strip() or "unknown"

    def _delete_docs(self, docs: list[dict], *, reason: str = "") -> int:
        deleted = 0
        for d in docs:
            mid = self._memory_id_of(d)
            if not mid:
                continue
            try:
                if self.store.delete_memory(mid):
                    deleted += 1
                    # Every deletion is an evolution event — the KB timeline
                    # must explain why a memory disappeared.
                    self.store.log_evolution(
                        mid, "prune",
                        utility_after=float(d.get("utility") or 0.0),
                        importance_after=float(d.get("importance") or 0.5),
                        access_count_after=int(d.get("access_count") or 0),
                        reason=reason or "批量删除",
                    )
            except Exception:
                logger.debug("memory_store._delete_docs failed mid=%s", mid, exc_info=True)
                continue
        return deleted

    @staticmethod
    def _overlap_tokens(text: str) -> set[str]:
        """Token set for overlap gathering: whitespace words, plus character
        bigrams for CJK runs (whitespace tokenization collapses a Chinese
        sentence into one token, which made conflict detection dead code for
        the app's dominant memory language)."""
        tokens: set[str] = set()
        for w in text.lower().split():
            if not w:
                continue
            tokens.add(w)
            if len(w) > 2 and any("一" <= ch <= "鿿" for ch in w):
                tokens.update(w[i : i + 2] for i in range(len(w) - 1))
        return tokens

    @classmethod
    def _word_overlap(cls, a: str, b: str) -> float:
        words_a = cls._overlap_tokens(a)
        words_b = cls._overlap_tokens(b)
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / max(len(words_a), len(words_b))

    def _llm_is_duplicate(self, a: str, b: str) -> bool:
        try:
            from ..llm.llm_service import get_llm
            from ...utils.common import parse_llm_json

            llm = get_llm()
            prompt = f"Are these two research notes semantically the same topic? Answer JSON only: {{\"duplicate\":true/false}}\n1: {a[:200]}\n2: {b[:200]}"
            txt = stateless_llm_chat(llm, None, prompt, temperature=0.0, max_tokens=30).strip()
            d = parse_llm_json(txt)
            return bool(d.get("duplicate", False)) if isinstance(d, dict) else False
        except Exception:
            logger.debug("memory_store._llm_is_duplicate failed", exc_info=True)
            return False

    def _maybe_link(self, mid: str, content: str, *, scope: str, paper_id: int | None, kind: str) -> None:
        """A-MEM-style link generation on write: judge which recent memories
        this new one is substantively related to, store the edges."""
        pol = self.config
        if kind not in pol.linkable_kinds:
            return
        try:
            candidates = [
                d for d in self._list_recent_docs(scope=scope, paper_id=paper_id, kinds=list(pol.linkable_kinds), limit_per_kind=pol.link_judge_candidates)
                if self._memory_id_of(d) != mid
            ][: pol.link_judge_candidates]
            if not candidates:
                return
            from .memory_judge import judge_memory_links

            links = judge_memory_links(new_content=content, candidates=candidates)
            if not links:
                return
            self.store.add_links(mid, links, reason="写入期关联判别")
            for dst in links:
                self.store.log_evolution(mid, "link", reason=f"关联记忆 {dst}")
        except Exception:
            logger.debug("memory_store._maybe_link failed", exc_info=True)

    def _absorb_into(self, doc: dict, *, content: str, importance: float, user_id: str, kind: str, props: dict, event: str, reason: str) -> str:
        """Fold the new content into the existing memory in place — reusing its
        id so accumulated utility/access stats survive (dedup-merge and
        contradiction-supersede share this single write path)."""
        old_mid = self._memory_id_of(doc)
        new_imp = max(float(importance), self._imp_of(doc))
        mid = self.store.add_memory(
            user_id=user_id,
            content=content,
            memory_type=kind,
            importance=new_imp,
            properties=props,
            memory_id=old_mid or None,
        )
        if old_mid:
            self.store.log_evolution(old_mid, event, reason=reason)
        return mid

    def add(
        self,
        *,
        scope: str,
        paper_id: int | None,
        kind: str,
        content: str,
        importance: float = 0.5,
        meta: dict[str, Any | None] = None,
    ) -> str:
        user_id = self._user_id_for(scope, paper_id)
        props = self._properties(scope, paper_id, kind, meta)

        existing = self._list_recent_docs(scope=scope, paper_id=paper_id, kinds=[kind], limit_per_kind=10)
        # Write-time conflict judgment (Mem0-style): gather similar candidates
        # by word overlap (gathering only), let the judge decide same/supersede/
        # keep. Fallback: the original overlap heuristic.
        pol = self.config
        similar: list[tuple[float, dict[str, Any]]] = []
        for doc in existing:
            ov = self._word_overlap(content, self._content_of(doc))
            if ov > pol.dedup_candidate_overlap:
                similar.append((ov, doc))
        if similar:
            similar.sort(key=lambda x: -x[0])
            from .memory_judge import judge_write_conflict

            verdicts = judge_write_conflict(
                new_content=content,
                candidates=[d for _ov, d in similar[: pol.conflict_judge_candidates]],
            )
            if verdicts is None:
                ov, doc = similar[0]
                if ov > pol.dedup_merge_overlap or (
                    ov > pol.dedup_gray_zone_overlap and self._llm_is_duplicate(content, self._content_of(doc))
                ):
                    return self._absorb_into(doc, content=content, importance=importance, user_id=user_id, kind=kind, props=props,
                                             event="merge", reason="写入期判重合并")
            else:
                for _ov, doc in similar[: pol.conflict_judge_candidates]:
                    old_mid = self._memory_id_of(doc)
                    action = verdicts.get(old_mid or "", "keep")
                    if action == "same":
                        return self._absorb_into(doc, content=content, importance=importance, user_id=user_id, kind=kind, props=props,
                                                 event="merge", reason="写入期判重合并")
                    if action == "supersede":
                        return self._absorb_into(doc, content=content, importance=importance, user_id=user_id, kind=kind, props=props,
                                                 event="supersede", reason=f"被新记忆取代: {content[:80]}")
                # all "keep" → fall through to a fresh add

        result = self.store.add_memory(
            user_id=user_id,
            content=content,
            memory_type=kind,
            importance=float(importance),
            metadata=props,
        )
        self._maybe_link(result, content, scope=scope, paper_id=paper_id, kind=kind)

        if kind == "working":
            working_docs = self._list_recent_docs(scope=scope, paper_id=paper_id, kinds=["working"], limit_per_kind=100)
            if len(working_docs) > pol.working_compress_trigger:
                working_docs.sort(key=lambda d: self._ts_of(d), reverse=True)
                to_compress = working_docs[pol.working_compress_keep_head : pol.working_compress_keep_head + pol.working_compress_batch]
                if to_compress:
                    compressed = self._summarize_via_llm(to_compress)
                    if compressed:
                        # Delete exactly the batch that was summarized
                        self._delete_docs(to_compress, reason="working 超限压缩")
                        self.add(scope=scope, paper_id=paper_id, kind="long", content=compressed, importance=0.6)

        return result


    def upsert_single(
        self,
        *,
        scope: str,
        paper_id: int | None,
        kind: str,
        content: str,
        importance: float = 0.6,
        meta: dict[str, Any | None] = None,
    ) -> None:
        now = int(time.time())
        user_id = self._user_id_for(scope, paper_id)
        props = self._properties(scope, paper_id, kind, meta)
        memory_id = f"{user_id}:{kind}:singleton"
        self.store.add_memory(
            memory_id=memory_id,
            user_id=user_id,
            content=content,
            memory_type=kind,
            timestamp=now,
            importance=float(importance),
            properties=props,
        )

    def _list_recent_docs(
        self,
        *,
        scope: str,
        paper_id: int | None,
        kinds: list[str],
        limit_per_kind: int = 40,
    ) -> list[dict[str, Any]]:
        user_id = self._user_id_for(scope, paper_id)
        docs: list[dict[str, Any]] = []
        for k in [x for x in kinds if x.strip()]:
            try:
                items = self.store.search_memories(user_id=user_id, memory_type=k, limit=limit_per_kind)
            except Exception:
                items = []
            for d in items or []:
                if isinstance(d, dict):
                    docs.append(d)
        # Karpathy "position = last touched": rescue (injection → last_used)
        # floats a memory above its creation recency. Stable sort preserves the
        # SQL timestamp-DESC order as the final tiebreak.
        from .policy import attention_key
        return sorted(docs, key=attention_key, reverse=True)





    def _summarize_via_llm(self, docs: list[dict]) -> str:
        contents = [self._content_of(d) for d in docs if self._content_of(d)]
        if not contents:
            return ""
        batch = int(self.config.working_compress_batch)
        prompt = (
            "你是一个学术文献阅读助手的记忆管理模块。请将以下用户阅读过程中的关注点和问答摘要"
            "综合为一段不超过 300 字的紧凑摘要，保留关键术语、用户兴趣方向和研究问题。"
            "只输出摘要文本，不要加前缀或解释。\n\n"
            + "\n".join(f"- {c[:200]}" for c in contents[-batch:])
        )
        try:
            from ..llm.llm_service import get_llm
            llm = get_llm()
            summary = stateless_llm_chat(llm, None, prompt, temperature=0.3, max_tokens=400)
            return str(summary or "").strip()
        except Exception:
            logger.debug("memory_store._summarize_via_llm failed", exc_info=True)
            return ""

    def compress_working(self, *, scope: str = "paper", paper_id: int | None = None, min_entries: int = 6) -> str:
        entries = self._list_recent_docs(scope=scope, paper_id=paper_id, kinds=["working"], limit_per_kind=999)
        if len(entries) < min_entries:
            return "not enough entries to compress"

        to_compress = entries[5 : 5 + int(self.config.working_compress_batch)]
        if not to_compress:
            return "nothing to compress"
        compressed = self._summarize_via_llm(to_compress)
        if not compressed:
            return "compression failed"
        # Delete exactly the batch that was summarized — never more (silent loss)
        self._delete_docs(to_compress, reason="working 压缩为长期记忆")
        self.add(scope=scope, paper_id=paper_id, kind="long", content=compressed, importance=0.6)
        return "compressed"



    def list_recent_contents(
        self,
        *,
        scope: str,
        paper_id: int | None,
        kinds: list[str],
        limit: int = 10,
    ) -> list[str]:
        ks = [k for k in kinds if k.strip()]
        if not ks:
            return []
        user_id = self._user_id_for(scope, paper_id)
        out: list[tuple] = []
        for k in ks:
            docs = self.store.search_memories(user_id=user_id, memory_type=k, limit=limit)
            for d in docs:
                out.append((self._ts_of(d), self._content_of(d)))
        out.sort(key=lambda x: x[0], reverse=True)
        contents: list[str] = []
        for _, c in out:
            if c:
                contents.append(c)
            if len(contents) >= limit:
                break
        return contents

    def _collect_candidate_docs(
        self, *, scope: str, paper_id: int | None, kinds: list[str], limit_per_kind: int, label: str
    ) -> list[tuple[str, dict[str, Any]]]:
        """Fetch recent docs and project them onto ``(label, doc)`` pairs."""
        return [
            (label, doc)
            for doc in self._list_recent_docs(
                scope=scope, paper_id=paper_id, kinds=kinds, limit_per_kind=limit_per_kind,
            )
        ]

    def _rank_with_judge(
        self,
        docs: list[dict[str, Any]],
        *,
        purpose: str,
        context_hint: str = "",
        max_items: int,
    ) -> list[dict[str, Any]]:
        """Model-judged ranking of candidate memories; recency fallback.

        The judge decides which candidates are worth injecting and in what
        order — no hardcoded scoring formula. When the judge is unavailable
        (LLM unconfigured / disabled / error) fall back to recency order.
        """
        docs = [d for d in docs if self._content_of(d)]
        if not docs:
            return []
        from .memory_judge import rank_docs_with_judge

        ranked = rank_docs_with_judge(
            docs, purpose=purpose, context_hint=context_hint, max_items=max_items,
        )
        if ranked is None:
            docs.sort(key=lambda d: self._ts_of(d), reverse=True)
            return docs[: max(1, max_items)]
        return ranked

    def _semantic_recall_docs(
        self, *, paper_id: int, query: str, exclude_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Embedding-recalled docs across the FULL scope pool (long-tail memories
        the recency buckets miss). Empty when embeddings are unavailable."""
        from .memory_semantic import semantic_recall

        pool = self._list_recent_docs(scope="paper", paper_id=paper_id,
                                      kinds=["working", "short", "long", "paper_summary"], limit_per_kind=500)
        pool += self._list_recent_docs(scope="global", paper_id=None,
                                       kinds=["preference", "short", "long"], limit_per_kind=200)
        recalled = semantic_recall(self.store, docs=pool, query=query)
        return [d for d in recalled if self._memory_id_of(d) not in exclude_ids]

    def build_chat_memory(
        self,
        *,
        paper_id: int,
        query: str = "",
        context_hint: str = "",
        max_tokens: int = 1200,
        query_limit: int = 6,
    ) -> tuple[str, str, list[str]]:
        """One judged retrieval per chat turn. Returns
        ``(base_text, query_text, injected_memory_ids)``.

        Previously the base block (build_context_block) and the query block
        (get_context_for_query) each fired their own judge LLM call over
        overlapping pools. Now the union pool is ranked ONCE (the user query
        also guides the base-block selection) and sliced twice: the base block
        fills the token budget, the query block takes the top ``query_limit``
        of the remainder — no double injection, no exclude_ids plumbing.
        ``query`` is the user's message; ``context_hint`` (paper title) is the
        recall fallback for query-less openings.
        """
        _ttl = self.config.working_ttl_minutes
        _cutoff = int(time.time()) - _ttl * 60
        for _uid in ["papergraph:global"]:
            for _d in (self.store.search_memories(user_id=_uid, memory_type="working", limit=500) or []):
                if self._ts_of(_d) < _cutoff:
                    _mid = str(_d.get("memory_id"))
                    if self.store.delete_memory(_mid):
                        self.store.log_evolution(_mid, "prune", reason="working TTL 过期")

        # Bucketed candidate gathering keeps kind diversity; quotas sum to ≤
        # the judge candidate cap (24) so no bucket is truncated away —
        # working (the freshest Q&A context) must always reach the judge.
        buckets: list[tuple[str, dict[str, Any]]] = []
        buckets += self._collect_candidate_docs(
            scope="global", paper_id=None, kinds=["preference"], limit_per_kind=3, label="preference",
        )
        buckets += self._collect_candidate_docs(
            scope="global", paper_id=None, kinds=["reflection"], limit_per_kind=3, label="洞察",
        )
        buckets += self._collect_candidate_docs(
            scope="paper", paper_id=paper_id, kinds=["paper_summary", "long"], limit_per_kind=4, label="long",
        )
        buckets += self._collect_candidate_docs(
            scope="paper", paper_id=paper_id, kinds=["short", "working"], limit_per_kind=4, label="working",
        )
        label_of: dict[str, str] = {}
        docs: list[dict[str, Any]] = []
        for label, doc in buckets:
            mid = self._memory_id_of(doc)
            if mid:
                label_of[mid] = label
                docs.append(doc)
        # Semantic recall bucket: long-tail memories the recency buckets miss.
        # Prepended so the judge's candidate cap can't truncate them away
        # (they carry the query-relevance signal recency docs lack).
        recall_hint = (query or "").strip() or (context_hint or "").strip()
        if recall_hint:
            recalled_docs: list[dict[str, Any]] = []
            # Cap recalled docs so the union pool stays within the judge's
            # candidate cap (recall 6 + buckets 18 = 24).
            for doc in self._semantic_recall_docs(paper_id=paper_id, query=recall_hint, exclude_ids=set(label_of))[:6]:
                mid = self._memory_id_of(doc)
                if mid:
                    label_of[mid] = "recalled"
                    recalled_docs.append(doc)
            docs = recalled_docs + docs

        max_items = int(self.config.max_context_items) + max(1, int(query_limit))
        ranked = self._rank_with_judge(
            docs,
            purpose="论文阅读对话的记忆注入（全局偏好/元记忆洞察/论文摘要/近期问答/语义召回）",
            context_hint=recall_hint,
            max_items=max_items,
        )

        chars_per_token = int(self.config.chars_per_token_estimate)
        selected: list[str] = []
        injected_ids: list[str] = []
        budget = 0
        for doc in ranked:
            if len(injected_ids) >= int(self.config.max_context_items):
                break
            content = self._content_of(doc)
            if not content:
                continue
            cost = len(content) // chars_per_token
            if budget + cost > max_tokens:
                continue
            mid = self._memory_id_of(doc)
            label = label_of.get(mid or "", "working")
            selected.append(f"[{label}] {content}")
            budget += cost
            if mid:
                injected_ids.append(mid)

        # Query slice next (before link expansion, so links can't poach its
        # top candidates): top of the remainder, sharing the same token budget
        # — never re-injecting base picks.
        query_lines: list[str] = []
        if (query or "").strip():
            for doc in ranked:
                mid = self._memory_id_of(doc)
                if not mid or mid in injected_ids:
                    continue
                content = self._content_of(doc)
                if not content:
                    continue
                cost = len(content[:260]) // chars_per_token
                if budget + cost > max_tokens:
                    continue
                query_lines.append(f"- {content[:260]}")
                budget += cost
                injected_ids.append(mid)
                if len(query_lines) >= max(1, int(query_limit)):
                    break

        # A-MEM associative expansion: 1-hop neighbors of the judged selection
        # surface as [关联] memories while token budget remains.
        try:
            link_map = self.store.linked_ids(injected_ids)
            linked_extra: list[str] = []
            for mid in injected_ids:
                for nid in link_map.get(mid, []):
                    if nid not in injected_ids and nid not in linked_extra:
                        linked_extra.append(nid)
            for doc in self.store.get_memories_by_ids(linked_extra[: self.config.link_expansion_max]):
                content = str(doc.get("content") or "")
                cost = len(content) // chars_per_token
                if not content or budget + cost > max_tokens:
                    continue
                mid = str(doc.get("memory_id") or "")
                selected.append(f"[关联] {content}")
                budget += cost
                if mid:
                    injected_ids.append(mid)
        except Exception:
            logger.debug("build_chat_memory link expansion failed", exc_info=True)

        if injected_ids:
            self.store.increment_access(injected_ids)
        base_text = "\n".join(selected) if selected else ""
        query_text = "【与当前问题相关的记忆（检索匹配）】\n" + "\n".join(query_lines) if query_lines else ""
        return base_text, query_text, injected_ids

    def adjust_utility(self, memory_id: str, *, delta: float, event: str = "feedback", reason: str = "") -> bool:
        return self.store.adjust_utility(memory_id, delta=delta, event=event, reason=reason)

    def extract_memory_via_llm(self, paper_id: int, user_msg: str, assistant_reply: str) -> None:
        from ..llm.llm_service import get_llm

        prev_pref = self.list_recent_contents(scope="global", paper_id=None, kinds=["preference"], limit=1)
        prev_short = self.list_recent_contents(scope="paper", paper_id=paper_id, kinds=["short"], limit=8)

        prompt = (
            '你是论文阅读助手的"记忆抽取器"。\n'
            "请从对话中抽取值得写入记忆的内容，输出且仅输出一个 JSON：\n"
            '{"paper_short": [{"content": "...", "importance": 0.0}], '
            '"paper_long_update": {"content": "..."} | null, '
            '"global_preference_append": ["..."]}\n'
            "规则：paper_short 最多3条，每条<=60字；paper_long_update <=220字；global_preference_append 最多2条。"
        )

        user = (
            f"【已有全局偏好】\n{(prev_pref[0] if prev_pref else '（无）')}\n\n"
            f"【该论文已有近期记忆】\n{' '.join(f'- {x}' for x in prev_short if x)}\n\n"
            f"【用户最新问题】\n{user_msg}\n\n"
            f"【助手回复】\n{(assistant_reply or '')[:1200]}"
        )

        data = run_json_task(
            task_name="memory_extract",
            agent_name="papergraph_memory_extractor",
            llm=get_llm(),
            system_prompt=prompt,
            user_prompt=user,
            timeout_sec=12.0,
            retries=1,
            default={},
        )

        for it in (data.get("paper_short") or [])[:3]:
            if isinstance(it, dict) and (c := self._content_of(it)):
                imp = float(it.get("importance") or self.config.default_importance)
                self.add(scope="paper", paper_id=paper_id, kind="short", content=c[:240], importance=max(0.0, min(1.0, imp)))

        if isinstance(plu := data.get("paper_long_update"), dict) and (c := self._content_of(plu)):
            self.upsert_single(scope="paper", paper_id=paper_id, kind="paper_summary", content=c[:900], importance=self.config.paper_summary_importance)

        if adds := [f"- {str(x).strip()[:240]}" for x in (data.get("global_preference_append") or [])[:2] if str(x).strip()]:
            prev = self.list_recent_contents(scope="global", paper_id=None, kinds=["preference"], limit=1)
            base = (prev[0] if prev else "").strip()
            merged = "\n".join(((base + "\n" if base else "") + "\n".join(adds)).splitlines()[-40:])
            self.upsert_single(scope="global", paper_id=None, kind="preference", content=merged, importance=self.config.preference_importance)
