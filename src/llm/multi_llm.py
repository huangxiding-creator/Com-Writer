"""多厂商大模型统一客户端 —— 智谱GLM + DeepSeek 双引擎。

策略（借鉴 We-AIPO multi_llm.py）：
- 默认 GLM 免费优先（省钱）
- GLM 全部限流 → 切 DeepSeek
- prefer_paid=True → GLM-5.2 优先（质量）
- prefer='deepseek' → DeepSeek 优先

用法：
    client = MultiLLMClient(cfg)
    reply = client.chat(system, user, json_mode=True)
    reply = client.chat(system, user, prefer_paid=True)  # 质量优先
"""
from __future__ import annotations

from typing import Optional

import requests

from ..utils.logger import get_logger
from .zhipu import ZhipuClient, _RateLimited
from .deepseek import DeepSeekClient

_log = get_logger("llm.multi")


class MultiLLMClient:
    """多厂商大模型统一客户端。"""

    def __init__(self, cfg):
        self._cfg = cfg

        # 初始化智谱 GLM
        zhipu_key = cfg.get("智谱", "api_key", "") or ""
        free_models = cfg.get_list("智谱", "免费模型", ["glm-4-flashx", "glm-4-flash"])
        paid_model = cfg.get("智谱", "付费模型", "glm-5.2")
        zhipu_timeout = cfg.get_int("智谱", "请求超时秒", 120)
        self._zhipu: Optional[ZhipuClient] = (
            ZhipuClient(zhipu_key, free_models, paid_model, zhipu_timeout)
            if zhipu_key
            else None
        )

        # 初始化 DeepSeek
        deepseek_key = cfg.get("DeepSeek", "api_key", "") or ""
        deepseek_model = cfg.get("DeepSeek", "模型", "deepseek-chat")
        deepseek_timeout = cfg.get_int("DeepSeek", "请求超时秒", 60)
        self._deepseek: Optional[DeepSeekClient] = (
            DeepSeekClient(deepseek_key, deepseek_model, deepseek_timeout)
            if deepseek_key
            else None
        )

        if not self._zhipu and not self._deepseek:
            raise ValueError("智谱和 DeepSeek 都未配置 API Key")

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
        temperature: float = 0.3,
        prefer_paid: bool = False,
        prefer: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """统一调用接口。

        Args:
            prefer: 'deepseek' = DeepSeek优先；'' 或 'glm' = GLM优先（默认）
            prefer_paid: True 时用付费 GLM-5.2（质量优先）
        """
        if prefer == "deepseek":
            return self._chat_deepseek_first(
                system_prompt, user_prompt, json_mode, temperature, max_tokens
            )
        return self._chat_glm_first(
            system_prompt, user_prompt, json_mode, temperature, prefer_paid, max_tokens
        )

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        prefer_paid: bool = False,
        prefer: str = "",
    ) -> dict | list:
        """调用并返回解析后的 JSON 对象。"""
        from .json_utils import extract_json

        raw = self.chat(
            system_prompt,
            user_prompt,
            json_mode=True,
            temperature=temperature,
            prefer_paid=prefer_paid,
            prefer=prefer,
        )
        return extract_json(raw)

    def _chat_glm_first(
        self,
        system: str,
        user: str,
        json_mode: bool,
        temperature: float,
        prefer_paid: bool,
        max_tokens: int,
    ) -> str:
        """GLM 优先，限流时降级 DeepSeek。"""
        if self._zhipu:
            try:
                return self._zhipu.chat(
                    system, user, json_mode, temperature, prefer_paid, max_tokens
                )
            except _RateLimited:
                _log.warning("GLM 全部模型限流，切换 DeepSeek")
            except requests.RequestException as exc:
                _log.warning("GLM 调用失败，切换 DeepSeek: %s", str(exc)[:80])

        return self._call_deepseek(system, user, json_mode, temperature, max_tokens)

    def _chat_deepseek_first(
        self,
        system: str,
        user: str,
        json_mode: bool,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """DeepSeek 优先，失败时降级 GLM。"""
        try:
            return self._call_deepseek(system, user, json_mode, temperature, max_tokens)
        except requests.RequestException as exc:
            _log.warning("DeepSeek 调用失败，切换 GLM: %s", str(exc)[:80])
            if self._zhipu:
                return self._zhipu.chat(system, user, json_mode, temperature, False, max_tokens)
            raise

    def _call_deepseek(
        self, system: str, user: str, json_mode: bool, temperature: float, max_tokens: int
    ) -> str:
        if not self._deepseek:
            raise RuntimeError("DeepSeek 未配置")
        return self._deepseek.chat(system, user, json_mode, temperature, max_tokens)


def create_llm(cfg) -> MultiLLMClient:
    """工厂函数：创建多模型客户端。"""
    return MultiLLMClient(cfg)
