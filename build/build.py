#!/usr/bin/env python3
"""一键打包脚本 —— 构建 Com-Writer 绿色便携版。

用法:
  python build/build.py

输出:
  dist/Com-Writer/  — 可直接拷贝到其他电脑运行
"""
from __future__ import annotations

import subprocess
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    print("=" * 60)
    print("  Com-Writer 绿色便携版打包")
    print("=" * 60)

    # 清理旧构建
    dist = ROOT / "dist"
    build_cache = ROOT / "build" / "__pycache__"
    for p in [dist / "Com-Writer"]:
        if p.exists():
            print(f"清理: {p}")
            shutil.rmtree(p, ignore_errors=True)

    # 执行 PyInstaller
    spec = ROOT / "build" / "com_writer.spec"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(spec),
        "--noconfirm",
        "--distpath", str(dist),
        "--workpath", str(ROOT / "build" / "pyinstaller_cache"),
    ]
    print(f"\n执行: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, cwd=str(ROOT))

    if result.returncode != 0:
        print("\n❌ 打包失败！")
        sys.exit(1)

    output_dir = dist / "Com-Writer"
    if output_dir.exists():
        # 复制配置模板
        for fname in ["config.example.ini", ".env.example", "README.md"]:
            src = ROOT / fname
            if src.exists():
                shutil.copy2(str(src), str(output_dir / fname))

        print(f"\n✅ 打包成功！")
        print(f"📂 输出目录: {output_dir}")
        print(f"🚀 启动: {output_dir / 'Com-Writer.exe'}")
    else:
        print("\n❌ 输出目录不存在，打包可能失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
