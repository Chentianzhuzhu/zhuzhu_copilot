# -*- coding: utf-8 -*-
"""在 agent_panel.py 添加 TTS 按钮和 _open_tts_panel 方法"""
import sys

path = r'C:\Users\zhuzhu\Desktop\my first android app\src\zhuzhu_Copilot\ui\agent_panel.py'
lines = open(path, encoding='utf-8').read().split('\n')

# 1. 在 import 区添加 tts_panel 导入（在 agent_tools 那行之后）
for i, l in enumerate(lines):
    if 'agent_tools' in l and 'agent_screen' in l:
        insert_pos = i
        break

new_import = 'from zhuzhu_Copilot.ui.tts_panel import TtsPanel'
lines.insert(insert_pos + 1, new_import)

# 2. 在 bottom.addWidget(self.input, 1) 后插入 tts_btn
for i, l in enumerate(lines):
    if 'bottom.addWidget(self.input, 1)' in l:
        tts_code = '''
        # TTS 语音合成快捷入口
        self.tts_btn = QPushButton("TTS")
        self.tts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tts_btn.setAutoDefault(False)
        self.tts_btn.setFixedSize(42, 42)
        self.tts_btn.setStyleSheet(
            f"QPushButton {{ background: {PANEL}; border: 1px solid {BORDER};"
            f"border-radius: 21px; color: {TEXT}; font-size: 13px; }}"
            f"QPushButton:hover {{ border: 1px solid {ACCENT}; }}")
        self.tts_btn.setToolTip("Qwen-TTS 声音复刻")
        self.tts_btn.clicked.connect(self._open_tts_panel)
        bottom.addWidget(self.tts_btn)
'''
        lines.insert(i + 1, tts_code)
        break

# 3. 在 closeEvent 前添加 _open_tts_panel 方法
for i, l in enumerate(lines):
    if 'def closeEvent(self, event)' in l:
        tts_method = '''
    def _open_tts_panel(self):
        """打开 TTS 语音合成面板"""
        try:
            from zhuzhu_Copilot.ui.tts_panel import TtsPanel
            if not hasattr(self, '_tts_panel') or self._tts_panel is None:
                self._tts_panel = TtsPanel(self)
            self._tts_panel.show()
            self._tts_panel.raise_()
            self._tts_panel.activateWindow()
        except Exception as e:
            print(f"TTS 面板打开失败: {e}")
            return
'''
        lines.insert(i, tts_method)
        break

open(path, 'w', encoding='utf-8').write('\n'.join(lines))
print('done')
