# -*- coding: utf-8 -*-
"""诊断2：主窗口菜单栏/托盘名称 + 设置面板音色删除/刷新逻辑"""
import sys, re

base = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator'

# A) 主窗口菜单栏 / 托盘
p = base + r'\ui\main_window.py'
lines = open(p, encoding='utf-8').read().splitlines()
sys.stdout.write('===== main_window.py 菜单/托盘相关 =====\n')
for i, l in enumerate(lines):
    if re.search(r'QMenuBar|addMenu|QMenu\(|QSystemTrayIcon|setContextMenu|QAction\(|"zhuzhu', l):
        sys.stdout.write('%d: %s\n' % (i + 1, l.rstrip()[:130]))

# B) 设置面板 音色 相关（刷新/删除/选择/tts.json 写入）
p2 = base + r'\ui\agent_panel.py'
lines2 = open(p2, encoding='utf-8').read().splitlines()
sys.stdout.write('\n===== agent_panel.py 音色相关 =====\n')
for i, l in enumerate(lines2):
    if re.search(r'voice|音色|tts\.json|preferred_name|list_voices|delete_voice|刷新', l):
        sys.stdout.write('%d: %s\n' % (i + 1, l.rstrip()[:130]))
sys.stdout.flush()
