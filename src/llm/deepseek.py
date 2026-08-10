"""DeepSeek 客户端 —— 备选 LLM 引擎。

借鉴 We-AIPO multi_llm.py 的 DeepSeek 调用模式。
"""
from __future__ import annotations

from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..utils.logger import get_logger
from .json_utils import extract_json

_log = get_logger("llm.deepseek")

_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


class DeepSeekClient:
    """DeepSeek API 客户端。"""

    def __init__(self, api_key: str, model: str = "deepseek-chat", timeout: int = 60):
        if not api_key:
            raise ValueError("DeepSeek API Key 为空")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._session = requests.Session()

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> str:
        """调用 DeepSeek API。"""
        return self._call_with_retry(
            system_prompt, user_prompt, json_mode, temperature, max_tokens
        )

    @retry(
        retry=retry_if_exception_type((requests.RequestException,)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=15),
        reraise=True,
    )
    def _call_with_retry(
        self, system: str, user: str, json_mode: bool, temperature: float, max_tokens: int
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = self._session.post(
            _DEEPSEEK_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout,
        )

        if resp.status_code == 429:
            raise requests.RequestException("DeepSeek 429 限流")
        if resp.status_code != 200:
            raise requests.RequestException(
                f"DeepSeek HTTP {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
    ) -> dict | list:
        """调用并返回解析后的 JSON。"""
        raw = self.chat(system_prompt, user_prompt, json_mode=True, temperature=temperature)
        return extract_json(raw)
