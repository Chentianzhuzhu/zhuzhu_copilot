# -*- coding: utf-8 -*-
"""冒烟测试：离屏实例化 _AgentSettingsDialog，验证音色页与选择写入 tts.json"""
import os, sys, json, pathlib
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, r'C:\Users\zhuzhu\Desktop\my first android app\src')

from PyQt6.QtWidgets import QApplication
app = QApplication([])

from winapp_migrator.ui.agent_panel import _AgentSettingsDialog
dlg = _AgentSettingsDialog()
sys.stdout.write('构造OK\n')
sys.stdout.write('combo count=%d\n' % dlg.voice_combo.count())
for i in range(dlg.voice_combo.count()):
    sys.stdout.write('  item[%d] text=%r data=%r\n' % (i, dlg.voice_combo.itemText(i), dlg.voice_combo.itemData(i)))
sys.stdout.write('current=%r\n' % dlg.voice_combo.currentData())
sys.stdout.write('status=%s\n' % dlg.voice_status.text())
# 手动触发一次选择（选择当前项，值不变，仅验证写入链路）
dlg._on_voice_changed(dlg.voice_combo.currentIndex())
cfg = json.loads((pathlib.Path.home() / '.winapp_migrator' / 'agent' / 'tts.json').read_text(encoding='utf-8'))
sys.stdout.write('tts.json voice_id=%r\n' % cfg.get('voice_id'))
# 验证 _save 不报错（保存设置到 settings.json，此处仅冒烟——先备份后还原）
import shutil
sjson = pathlib.Path.home() / '.winapp_migrator' / 'agent' / 'settings.json'
bak = None
if sjson.exists():
    bak = sjson.read_text(encoding='utf-8')
try:
    dlg._save()
    sys.stdout.write('_save OK\n')
finally:
    if bak is not None:
        sjson.write_text(bak, encoding='utf-8')
dlg.close()
sys.stdout.write('SMOKE_OK\n')
sys.stdout.flush()
