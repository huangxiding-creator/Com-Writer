# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 企业写手 Com-Writer 绿色便携版。

打包命令:
  pyinstaller build/com_writer.spec --noconfirm

输出:
  dist/Com-Writer/ 目录（可直接拷贝到其他电脑运行）
"""
import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['com_writer.py'],
    pathex=[],
    binaries=[],
    datas=[
        # 携带默认配置模板
        ('config.example.ini', '.'),
        ('.env.example', '.'),
        ('README.md', '.'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'src',
        'src.gui',
        'src.gui.main_window',
        'src.gui.widgets',
        'src.gui.settings',
        'src.gui.recorder',
        'src.pipeline',
        'src.pipeline.auto_pipeline',
        'src.workspace',
        'src.workspace.manager',
        'src.config',
        'src.config.loader',
        'src.config.paths',
        'src.core',
        'src.core.models',
        'src.core.orchestrator',
        'src.llm',
        'src.llm.multi_llm',
        'src.llm.zhipu',
        'src.llm.deepseek',
        'src.processors',
        'src.processors.understander',
        'src.processors.generator',
        'src.processors.refiner',
        'src.processors.post_processor',
        'src.readers',
        'src.readers.docx_reader',
        'src.readers.transcript_parser',
        'src.writers',
        'src.writers.docx_writer',
        'src.writers.template_engine',
        'src.notify',
        'src.notify.wecom',
        'src.utils',
        'src.utils.logger',
        'src.utils.text',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'pandas',
        'IPython',
        'pytest',
        'jupyter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Com-Writer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # GUI 模式不显示控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='build/icon.ico' if Path('build/icon.ico').exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Com-Writer',
)
