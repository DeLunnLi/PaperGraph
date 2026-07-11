
import logging
import os
from typing import Any

from .client import LLMClient, ChatResult
from ...settings import get_settings

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# 历史背景：原本此处有两个 monkey-patch ——
#   (a) _patch_hello_agents_llm_openai_chat_roles  角色归一化（summary/developer）
#   (b) _patch_deepseek_disable_thinking           DeepSeek thinking 禁用
# 现已由 LLMClient._normalize_messages / LLMClient._merge_extra 收敛，不再打补丁。
# ----------------------------------------------------------------------

_llm_instance: LLMClient | None = None


def _maybe_disable_proxy_for_llm(base_url: str) -> None:
    url = (base_url or "").strip()
    if not url:
        return

    disable = get_settings().llm_disable_proxy
    proxy_vars = ["HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"]
    has_proxy = any(str(os.getenv(k) or "").strip() for k in proxy_vars)
    if not has_proxy:
        return

    from urllib.parse import urlparse
    host = ""
    try:
        host = (urlparse(url).hostname or "").strip()
    except Exception:
        host = ""
    if not host:
        return

    def _append_no_proxy(*extra_hosts: str) -> None:
        to_add = [h for h in (host, *extra_hosts) if h and str(h).strip()]

        if "deepseek.com" in host:
            for h in ("deepseek.com", "*.deepseek.com"):
                if h not in to_add:
                    to_add.append(h)

        if "aihubmix.com" in host:
            for h in ("aihubmix.com", "*.aihubmix.com"):
                if h not in to_add:
                    to_add.append(h)
        for env_key in ("NO_PROXY", "no_proxy"):
            cur = str(os.getenv(env_key) or "").strip()
            parts = [p.strip() for p in cur.split(",") if p.strip()]
            seen = {p.lower() for p in parts}
            for h in to_add:
                hl = h.lower()
                if hl not in seen:
                    parts.append(h)
                    seen.add(hl)
            os.environ[env_key] = ",".join(parts)

    if disable:
        for k in proxy_vars:
            os.environ.pop(k, None)
        _append_no_proxy()
        return

    _append_no_proxy()

def is_llm_configured() -> bool:
    s = get_settings()
    key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or s.openai_api_key or "").strip()
    if not key:

        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
            key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    return bool(key)

def _sync_env_from_settings() -> None:
    s = get_settings()

    if not os.getenv("LLM_API_KEY") and not os.getenv("OPENAI_API_KEY") and os.getenv("AIHUBMIX_API_KEY"):
        os.environ["LLM_API_KEY"] = str(os.getenv("AIHUBMIX_API_KEY") or "").strip()
    if not os.getenv("LLM_BASE_URL") and not os.getenv("OPENAI_BASE_URL") and os.getenv("AIHUBMIX_BASE_URL"):
        os.environ["LLM_BASE_URL"] = str(os.getenv("AIHUBMIX_BASE_URL") or "").strip()
    if not os.getenv("LLM_MODEL_ID") and not os.getenv("OPENAI_MODEL") and os.getenv("AIHUBMIX_MODEL_ID"):
        os.environ["LLM_MODEL_ID"] = str(os.getenv("AIHUBMIX_MODEL_ID") or "").strip()

    if not os.getenv("LLM_API_KEY") and not os.getenv("OPENAI_API_KEY") and s.openai_api_key:
        os.environ["LLM_API_KEY"] = s.openai_api_key
    if not os.getenv("LLM_BASE_URL") and not os.getenv("OPENAI_BASE_URL") and s.openai_base_url:
        os.environ["LLM_BASE_URL"] = s.openai_base_url
    if not os.getenv("LLM_MODEL_ID") and not os.getenv("OPENAI_MODEL") and s.openai_model:
        os.environ["LLM_MODEL_ID"] = s.openai_model

def get_llm() -> LLMClient:
    global _llm_instance
    if _llm_instance is None:

        _sync_env_from_settings()

        api_key = (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "")
        base_url = (os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "")
        model = (os.getenv("LLM_MODEL_ID") or os.getenv("OPENAI_MODEL") or "")

        if not api_key:
            s = get_settings()
            api_key = s.openai_api_key
            if not base_url:
                base_url = s.openai_base_url
            if not model:
                model = s.openai_model

        if not api_key:
            raise RuntimeError("LLM 未配置：请设置 LLM_API_KEY（或在 backend/.env 中配置）")

        _maybe_disable_proxy_for_llm(base_url)

        # 与原 HelloAgentsLLM 行为一致：支持 LLM_TIMEOUT env，默认 60s。
        try:
            timeout = int(os.getenv("LLM_TIMEOUT", "60"))
        except ValueError:
            timeout = 60

        kw = {}
        if model:
            kw["model"] = model
        if api_key:
            kw["api_key"] = api_key
        if base_url:
            kw["base_url"] = base_url
        kw["timeout"] = timeout

        logger.info("🔧 正在初始化 LLM...")
        logger.info("   Model: %s", model or "default")
        logger.info("   Base URL: %s", base_url or "default")

        _llm_instance = LLMClient(**kw)
        logger.info("✅ LLM 已初始化")
        logger.info("   实际模型: %s", getattr(_llm_instance, "model", "") or "unknown")
        logger.info("   Provider: %s", getattr(_llm_instance, "provider", None) or "unknown")
    return _llm_instance
