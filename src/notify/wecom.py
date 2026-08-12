"""企业微信通知 —— Webhook 推送（借鉴 We-AIPO wecom.py）。

支持 markdown、text、image、file 四种消息类型。
核心原则：通知失败绝不阻塞主流程。
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Optional

import requests

from ..utils.logger import get_logger

_log = get_logger("notify.wecom")


class WeComNotifier:
    """企业微信 Webhook 通知器。"""

    def __init__(self, webhook_url: str):
        self._url = webhook_url
        self._session = requests.Session()

    def send_text(self, content: str, mentioned: list[str] | None = None) -> bool:
        """发送文本消息。mentioned 为手机号列表时 @ 对应人员。"""
        body: dict = {
            "msgtype": "text",
            "text": {"content": content},
        }
        if mentioned:
            body["text"]["mentioned_list"] = mentioned
        return self._send(body)

    def send_markdown(self, content: str) -> bool:
        """发送 Markdown 消息。"""
        body = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        return self._send(body)

    def send_milestone(self, milestone: str, details: str = "") -> bool:
        """发送关键节点通知。"""
        md = f"## 📌 {milestone}\n"
        if details:
            md += f"\n{details}"
        return self.send_markdown(md)

    def send_completion(
        self,
        task_name: str,
        output_path: str = "",
        duration: float = 0.0,
        model_used: str = "",
        quality_score: int = 0,
    ) -> bool:
        """发送任务完成通知。"""
        parts = [f"## ✅ 任务完成: {task_name}"]
        if output_path:
            parts.append(f"📂 **输出文件**: `{output_path}`")
        if duration > 0:
            parts.append(f"⏱️ **耗时**: {duration:.1f}秒")
        if model_used:
            parts.append(f"🤖 **使用模型**: {model_used}")
        if quality_score > 0:
            parts.append(f"📊 **质量评分**: {quality_score}/100")
        parts.append(f"\n> 企业写手 Com-Writer | {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}")
        return self.send_markdown("\n".join(parts))

    def send_error(self, task_name: str, error_msg: str) -> bool:
        """发送错误告警。"""
        md = f"## ❌ 任务失败: {task_name}\n\n```\n{error_msg[:500]}\n```"
        return self.send_markdown(md)

    def send_image_file(self, image_path: str | Path) -> bool:
        """发送图片（自动转 base64 + md5）。"""
        path = Path(image_path)
        if not path.exists():
            _log.warning("图片不存在: %s", path)
            return False
        raw = path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        md5 = hashlib.md5(raw).hexdigest()
        body = {
            "msgtype": "image",
            "image": {"base64": b64, "md5": md5},
        }
        return self._send(body)

    def send_file(self, file_path: str | Path, media_id: str = "") -> bool:
        """发送文件消息。

        需先通过企业微信 media upload API 获取 media_id。
        如未提供 media_id，仅发送文件名通知。
        """
        path = Path(file_path)
        if media_id:
            body = {
                "msgtype": "file",
                "file": {"media_id": media_id},
            }
            return self._send(body)
        # 无 media_id：发送文件名提醒
        size_kb = path.stat().st_size / 1024 if path.exists() else 0
        return self.send_text(
            f"📎 文件已生成: {path.name} ({size_kb:.0f}KB)\n"
            f"📂 路径: {path.parent}"
        )

    def send_output_files(self, files: list[Path]) -> None:
        """推送写作成果通知（文件名列表 + 完成消息）。"""
        if not files:
            return
        names = "\n".join(f"📄 {Path(f).name}" for f in files)
        self.send_markdown(
            f"## ✅ 写作完成\n\n{names}\n\n"
            f"> 企业写手 Com-Writer"
        )

    def _send(self, body: dict) -> bool:
        """实际发送请求。失败不抛异常。"""
        if not self._url:
            _log.debug("Webhook URL 为空，跳过通知")
            return False
        try:
            resp = self._session.post(
                self._url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("errcode", 0) == 0:
                    _log.debug("企业微信通知发送成功")
                    return True
                _log.warning("企业微信返回错误: %s", data.get("errmsg", "未知"))
            else:
                _log.warning("企业微信 HTTP %d", resp.status_code)
            return False
        except Exception as exc:
            _log.warning("企业微信通知失败（不影响主流程）: %s", str(exc)[:100])
            return False


def create_notifier(webhook_url: str = "") -> Optional[WeComNotifier]:
    """工厂函数。webhook_url 为空则返回 None。"""
    if not webhook_url:
        return None
    return WeComNotifier(webhook_url)
