"""写作技能引擎 —— 以 Skill 文件形式管理不同体裁/场景的写作规则。

每个 Skill 是一个 YAML/Markdown 文件，包含：
- name: 技能名称
- genre: 体裁（会议纪要/函件/通知/汇报…）
- system_prompt: 系统提示词前缀
- rules: 写作规则列表
- style_notes: 风格说明
- post_rules: 后处理规则（正则替换）

设计原则：
- Skill 文件可热加载（修改后无需重启）
- 用户可在 settings 中查看和编辑已有 Skill
- 管线运行时自动匹配最相关的 Skill
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..utils.logger import get_logger

_log = get_logger("skills.engine")

SKILLS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class WritingSkill:
    """一个写作技能定义。"""
    name: str                       # 技能名（如 "会议纪要-调度会"）
    genre: str                      # 体裁（如 "会议纪要"）
    system_prompt: str              # LLM 系统提示词
    rules: tuple[str, ...]          # 写作规则
    style_notes: str                # 风格说明
    post_rules: tuple[tuple[str, str, str], ...] = ()  # (pattern, replacement, note)

    def to_prompt_section(self) -> str:
        """转换为 LLM prompt 片段。"""
        parts = [f"## 写作技能: {self.name}"]
        if self.style_notes:
            parts.append(f"\n### 风格要求\n{self.style_notes}")
        if self.rules:
            rules_text = "\n".join(f"{i+1}. {r}" for i, r in enumerate(self.rules))
            parts.append(f"\n### 写作规则\n{rules_text}")
        return "\n".join(parts)


def _parse_skill_file(path: Path) -> Optional[WritingSkill]:
    """解析 Skill 文件（简单 Markdown 格式）。

    格式约定:
        ---
        name: 技能名
        genre: 体裁
        ---
        ## 系统提示词
        ...
        ## 写作规则
        1. ...
        2. ...
        ## 风格说明
        ...
        ## 后处理规则
        pattern | replacement | note
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        _log.warning("读取 Skill 文件失败 %s: %s", path, e)
        return None

    # 解析 YAML frontmatter（简单键值对）
    name = path.stem
    genre = ""
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if fm_match:
        for line in fm_match.group(1).splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if key == "name":
                    name = val
                elif key == "genre":
                    genre = val
        text = text[fm_match.end():]

    # 解析 sections
    sections: dict[str, str] = {}
    current_section = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        h_match = re.match(r"^##\s+(.+)$", line)
        if h_match:
            if current_section:
                sections[current_section] = "\n".join(current_lines).strip()
            current_section = h_match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_section:
        sections[current_section] = "\n".join(current_lines).strip()

    system_prompt = sections.get("系统提示词", "")
    style_notes = sections.get("风格说明", "")

    # 写作规则
    rules_text = sections.get("写作规则", "")
    rules = tuple(
        re.sub(r"^\d+\.\s*", "", line.strip())
        for line in rules_text.splitlines()
        if line.strip() and re.match(r"^\d+\.", line.strip())
    )

    # 后处理规则
    post_text = sections.get("后处理规则", "")
    post_rules: list[tuple[str, str, str]] = []
    for line in post_text.splitlines():
        line = line.strip()
        if "|" in line and not line.startswith("#"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                pattern = parts[0]
                replacement = parts[1] if len(parts) > 1 else ""
                note = parts[2] if len(parts) > 2 else ""
                post_rules.append((pattern, replacement, note))

    return WritingSkill(
        name=name,
        genre=genre,
        system_prompt=system_prompt,
        rules=rules,
        style_notes=style_notes,
        post_rules=tuple(post_rules),
    )


class SkillRegistry:
    """技能注册中心 —— 加载、索引、匹配 Skill 文件。"""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, WritingSkill] = {}
        self._load_all()

    def _load_all(self) -> None:
        """加载目录下所有 .md Skill 文件。"""
        if not self._dir.exists():
            return
        for path in self._dir.rglob("*.md"):
            if path.name.startswith("_"):
                continue
            skill = _parse_skill_file(path)
            if skill:
                self._skills[skill.name] = skill
                _log.debug("加载 Skill: %s (%s)", skill.name, skill.genre)

    def reload(self) -> None:
        """重新加载所有 Skill（热重载）。"""
        self._skills.clear()
        self._load_all()

    def get(self, name: str) -> Optional[WritingSkill]:
        return self._skills.get(name)

    def all_skills(self) -> list[WritingSkill]:
        return list(self._skills.values())

    def by_genre(self, genre: str) -> list[WritingSkill]:
        return [s for s in self._skills.values() if s.genre == genre]

    def match(self, genre: str = "", sub_style: str = "") -> Optional[WritingSkill]:
        """根据体裁和子风格匹配最相关的 Skill。

        匹配优先级:
        1. name 包含 genre + sub_style
        2. genre 完全匹配
        3. 任意 Skill
        """
        if genre and sub_style:
            key = f"{genre}-{sub_style}"
            if key in self._skills:
                return self._skills[key]
            for name, skill in self._skills.items():
                if genre in name and sub_style in name:
                    return skill

        if genre:
            matches = self.by_genre(genre)
            if matches:
                return matches[0]

        if self._skills:
            return next(iter(self._skills.values()))
        return None
