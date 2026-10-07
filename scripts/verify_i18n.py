# -*- coding: utf-8 -*-
"""校验登录页 i18n：所有 data-i18n / data-i18n-ph 键在中英字典中都存在，
且 JS 中 t('...') 用到的键也已定义。"""
import re, sys, json

PATH = r"C:\Users\zhuzhu\Desktop\zhuzhu Copilot\auth-server\static\login\index.html"
with open(PATH, "r", encoding="utf-8") as f:
    html = f.read()

# 提取 I18N 对象（两段：'zh-CN': {...}, 'en': {...}）
m_zh = re.search(r"'zh-CN':\s*\{(.*?)\n\s*\},\n\s*'en':", html, re.S)
m_en = re.search(r"'en':\s*\{(.*?)\n\s*\}\n\};", html, re.S)
assert m_zh and m_en, "无法定位 I18N 字典"

def parse_dict(block):
    keys = {}
    for km in re.finditer(r"'([a-zA-Z0-9_.]+)':\s*'((?:[^'\\]|\\.)*)'", block):
        keys[km.group(1)] = km.group(2)
    return keys

zh = parse_dict(m_zh.group(1))
en = parse_dict(m_en.group(1))
print(f"zh keys: {len(zh)}  en keys: {len(en)}")

missing_en = sorted(set(zh) - set(en))
missing_zh = sorted(set(en) - set(zh))
print("missing in en:", missing_en)
print("missing in zh:", missing_zh)

# HTML data-i18n 属性
attrs = set(re.findall(r'data-i18n(?:-ph|-title)?="([^"]+)"', html))
print(f"HTML i18n attrs: {len(attrs)}")
bad_attr = sorted(k for k in attrs if k not in zh or k not in en)
print("HTML attrs missing keys:", bad_attr)

# JS t('...') 调用
js_keys = set(re.findall(r"\bt\('([a-zA-Z0-9_.]+)'", html))
js_keys.discard("")  # 排除 t( 误匹配
bad_js = sorted(k for k in js_keys if k not in zh or k not in en)
print(f"JS t() keys: {len(js_keys)}")
print("JS keys missing:", bad_js)

ok = not missing_en and not missing_zh and not bad_attr and not bad_js
print("I18N_COMPLETE:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
