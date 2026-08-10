"""智谱 GLM 客户端 —— 多免费模型链 + 自适应限速。

借鉴 We-AIPO src/llm/zhipu.py 的成熟设计：
- 免费模型链按序尝试，遇 429 切下一个
- _AdaptivePacer 自适应限速（成功加速 / 限流退避）
- JSON 模式支持
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from ..utils.logger import get_logger
from .json_utils import extract_json

_log = get_logger("llm.zhipu")

_ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"


class _AdaptivePacer:
    """自适应限速器：成功时快速恢复，限流时指数退避。"""

    def __init__(self, start: float = 3.0, cap: float = 40.0, floor: float = 1.0):
        self._interval = start
        self._cap = cap
        self._floor = floor
        self._last_call = 0.0

    def wait(self) -> None:
        now = time.time()
        elapsed = now - self._last_call
        if elapsed < self._interval:
            time.sleep(self._interval - elapsed)
        self._last_call = time.time()

    def on_success(self) -> None:
        self._interval = max(self._floor, self._interval * 0.7)

    def on_rate_limited(self) -> None:
        self._interval = min(self._cap, self._interval * 2.0)
        _log.warning("限流，退避至 %.1f 秒", self._interval)


class _RateLimited(Exception):
    """所有免费模型均被限流。"""


class ZhipuClient:
    """智谱 GLM 客户端。免费模型链降级 + 可选付费质量模型。"""

    def __init__(
        self,
        api_key: str,
        free_models: list[str],
        paid_model: str = "glm-5.2",
        timeout: int = 120,
    ):
        if not api_key:
            raise ValueError("智谱 API Key 为空")
        self._api_key = api_key
        self._free_models = free_models or ["glm-4-flashx", "glm-4-flash"]
        self._paid_model = paid_model
        self._timeout = timeout
        self._pacer = _AdaptivePacer()
        self._session = requests.Session()

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
        temperature: float = 0.3,
        prefer_paid: bool = False,
        max_tokens: int = 4096,
    ) -> str:
        """调用智谱 API，免费模型链降级。

        Args:
            prefer_paid: True 时优先使用付费模型（质量优先）
            json_mode: True 时要求 JSON 格式输出
            temperature: 0-1，越低越确定
            max_tokens: 最大输出 token 数
        Returns:
            LLM 回复文本
        Raises:
            _RateLimited: 所有模型均限流
            Exception: 其他 API 错误
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # 构建模型尝试链
        chain = list(self._free_models)
        if prefer_paid:
            chain.insert(0, self._paid_model)

        last_error: Optional[Exception] = None

        for i, model in enumerate(chain):
            is_last = i == len(chain) - 1
            try:
                self._pacer.wait()
                result = self._call_api(
                    messages, model, json_mode, temperature, max_tokens
                )
                self._pacer.on_success()
                _log.debug("模型 %s 调用成功", model)
                return result
            except _RateLimited:
                if is_last:
                    raise
                _log.warning("模型 %s 限流，切换下一个", model)
                continue
            except requests.RequestException as exc:
                last_error = exc
                _log.warning("模型 %s 网络错误: %s，切换下一个", model, str(exc)[:80])
                if is_last:
                    raise
                continue

        raise last_error or _RateLimited("所有模型均不可用")

    def _call_api(
        self,
        messages: list[dict],
        model: str,
        json_mode: bool,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """调用单个模型的 API。"""
        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = self._session.post(
            _ZHIPU_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout,
        )

        if resp.status_code == 429:
            self._pacer.on_rate_limited()
            raise _RateLimited(f"模型 {model} 限流 (429)")

        if resp.status_code != 200:
            error_text = resp.text[:200] if resp.text else "无响应体"
            raise requests.RequestException(
                f"智谱 HTTP {resp.status_code}: {error_text}"
            )

        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        if not content or not content.strip():
            raise requests.RequestException(
                f"模型 {model} 返回空响应（可能请求过长或触发内容过滤）"
            )

        return content

    def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.3,
        prefer_paid: bool = False,
    ) -> dict | list:
        """调用并直接返回解析后的 JSON 对象。"""
        raw = self.chat(
            system_prompt,
            user_prompt,
            json_mode=True,
            temperature=temperature,
            prefer_paid=prefer_paid,
        )
        return extract_json(raw)
