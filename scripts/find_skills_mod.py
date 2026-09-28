# -*- coding: utf-8 -*-
"""查看 agent_skills.load_settings / save_settings 实现"""
import os
# 找 agent_skills.py
for root, dirs, files in os.walk(r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot'):
    for f in files:
        if f == 'agent_skills.py':
            print(os.path.join(root, f))
