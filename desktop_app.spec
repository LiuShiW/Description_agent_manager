# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ["autogen_agentchat", "autogen_ext", "autogen_core", "openai",
            "yaml", "pydantic", "tiktoken",
            "pymilvus", "milvus_lite",        # RAG 向量库（Milvus Lite 嵌入式）
            "fastembed", "onnxruntime"]:       # RAG 语义向量（可选，装了才收集）
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["app/desktop_app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas + [("config", "config")],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# 过滤掉 PyArrow/Pandas 等带入的 ICU DLL（它们导出带版本后缀的符号，
# 会抢占 Windows 系统 ICU，导致 Qt6Core 报“找不到指定的程序”）。
def _is_icu(path):
    name = os.path.basename(path).lower()
    return name.startswith("icu") and name.endswith(".dll")

a.binaries = [b for b in a.binaries if not _is_icu(b[0])]
a.datas = [d for d in a.datas if not _is_icu(d[0])]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI软件团队",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AI软件团队",
)
