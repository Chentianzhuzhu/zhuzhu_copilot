# -*- coding: utf-8 -*-
"""验证：语法编译 + 修改点检查"""
import sys, py_compile

files = [
    r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_tts.py',
    r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py',
]
for f in files:
    try:
        py_compile.compile(f, doraise=True)
        sys.stdout.write('COMPILE_OK: %s\n' % f.split('winapp_migrator')[-1])
    except Exception as e:
        sys.stdout.write('COMPILE_FAIL: %s -> %s\n' % (f, e))

P = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py'
src = open(P, encoding='utf-8').read()
checks = [
    ('import TtsPanel 已移除', 'from winapp_migrator.ui.tts_panel import TtsPanel' not in src),
    ('agent_tts 已引入', 'agent_tts' in src.split('from winapp_migrator.ui.tts_panel')[0] or 'agent_tts' in src),
    ('tts_btn 已移除', 'tts_btn' not in src),
    ('_open_tts_panel 已移除', '_open_tts_panel' not in src),
    ('mic 图标已加', 'kind == "mic"' in src),
    ('导航"语音合成"已加', '("语音合成", "mic"),' in src),
    ('_build_tts_page 已加', 'def _build_tts_page' in src),
    ('_reload_voices 已加', 'def _reload_voices' in src),
    ('_on_voice_changed 已加', 'def _on_voice_changed' in src),
    ('_save 同步音色已加', 'voice_combo' in src and 'save_config(voice_id=vid)' in src),
    ('_build_skill_page 仍存在', 'def _build_skill_page' in src),
    ('_build_mcp_page 仍存在', 'def _build_mcp_page' in src),
]
for name, ok in checks:
    sys.stdout.write('%s: %s\n' % ('PASS' if ok else 'FAIL', name))

# agent_tts 检查
T = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\core\agent_tts.py'
tsrc = open(T, encoding='utf-8').read()
tchecks = [
    ('list_voices 已加', 'def list_voices' in tsrc),
    ('synthesize 回退配置', 'load_config().get("voice_id"' in tsrc),
]
for name, ok in tchecks:
    sys.stdout.write('%s: %s\n' % ('PASS' if ok else 'FAIL', name))
sys.stdout.flush()
