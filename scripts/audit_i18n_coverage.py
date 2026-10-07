# -*- coding: utf-8 -*-
"""全量审计：扫描客户端所有 `_ui()` 调用点，找出语言包 en_US.json 缺失的 key。

英文模式下 `_ui(key)` 查不到就回落中文原文 —— 这正是「English 模式仍有中文硬编码」
的真实成因（并非漏包 _ui，而是包缺条目）。本脚本把缺口一次性列全。
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PKG = os.path.join(SRC, "zhuzhu_Copilot")
PACK = os.path.join(PKG, "locales", "en_US.json")

CJK = re.compile(r"[\u4e00-\u9fff]")
UI_FUNCS = {"_ui", "_uif", "_uim", "uim", "ui"}

pack = json.load(io.open(PACK, encoding="utf-8")).get("ui", {})

missing: dict[str, list[str]] = {}     # key -> [files]
used: dict[str, int] = {}
scanned = 0

for dirpath, dirnames, filenames in os.walk(PKG):
    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "locales")]
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        path = os.path.join(dirpath, fn)
        rel = os.path.relpath(path, SRC).replace(os.sep, "/")
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except Exception:
            continue
        scanned += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else "")
            if name not in UI_FUNCS:
                continue
            # 仅取第一个常量字符串参数（i18n key）
            if not node.args:
                continue
            a0 = node.args[0]
            # 处理常量拼接 (ast.Constant) 与 f-string/隐式拼接
            key = None
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                key = a0.value
            elif isinstance(a0, ast.JoinedStr):
                key = "<f-string>"
            if key is None or not key:
                continue
            used[key] = used.get(key, 0) + 1
            if key == "<f-string>":
                continue
            if CJK.search(key) and key not in pack:
                missing.setdefault(key, [])
                if rel not in missing[key]:
                    missing[key].append(rel)

print(f"扫描 .py 文件: {scanned}")
print(f"_ui 调用点去重 key: {len(used)}")
print(f"包内条目: {len(pack)}")
print(f"缺翻译的中文 key: {len(missing)}")
print()
if missing:
    for k in sorted(missing):
        files = ", ".join(missing[k][:2])
        print(f"  MISS  {k!r}   [{files}]")
else:
    print("（无缺口）")

print()
print("I18N_COVERAGE", "FAIL" if missing else "PASS")
sys.exit(1 if missing else 0)
