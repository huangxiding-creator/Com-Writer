"""检查点恢复 —— JSON 状态持久化，中断后自动恢复。

借鉴 We-AIPO 的 checkpoint 机制：
- 每步完成后保存状态
- 重启时检测未完成任务
- 从最后完成的步骤继续
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils.logger import get_logger
from ..config.paths import STATE_FILE

_log = get_logger("core.checkpoint")


def save_state(task_id: str, phase: str, data: dict[str, Any]) -> None:
    """保存检查点状态。

    Args:
        task_id: 任务标识（如 "meeting_minutes:文件名"）
        phase: 当前阶段（understanding/generation/quality/writing/done）
        data: 阶段数据（中间结果）
    """
    state = _load_state()
    state[task_id] = {
        "phase": phase,
        "data": data,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        _log.warning("检查点保存失败: %s", exc)


def load_task_state(task_id: str) -> dict[str, Any] | None:
    """加载指定任务的状态。返回 None 表示无历史状态。"""
    state = _load_state()
    return state.get(task_id)


def clear_task_state(task_id: str) -> None:
    """清除指定任务的状态（完成后调用）。"""
    state = _load_state()
    if task_id in state:
        del state[task_id]
        try:
            STATE_FILE.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            _log.warning("检查点清除失败: %s", exc)


def _load_state() -> dict[str, Any]:
    """读取状态文件。"""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
