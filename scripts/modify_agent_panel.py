# -*- coding: utf-8 -*-
"""
修改 agent_panel.py：
1. 移除 import TtsPanel，并引入 agent_tts
2. 删除 tts_btn 块（独立 TTS 配置入口）
3. 删除 _open_tts_panel 方法
4. _line_icon 增加 "mic" 图标分支
5. _AgentSettingsDialog 导航增加"语音合成"页 + _build_tts_page 方法 + _save 保存音色
"""
import sys

P = r'C:\Users\zhuzhu\Desktop\my first android app\src\winapp_migrator\ui\agent_panel.py'
src = open(P, encoding='utf-8').read()
lines = src.splitlines(keepends=True)


def line_index(pred, start=0):
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    return -1


def bare(l):
    return l.rstrip('\r\n')


log = []

# ---------- 1. import ----------
i = line_index(lambda l: 'from winapp_migrator.ui.tts_panel import TtsPanel' in l)
assert i >= 0, 'import TtsPanel not found'
del lines[i]
j = line_index(lambda l: bare(l).startswith('from winapp_migrator.core import agent_llm'))
assert j >= 0, 'agent core import not found'
if 'agent_tts' not in lines[j]:
    lines[j] = bare(lines[j]) + ', agent_tts' + lines[j][len(bare(lines[j])):]
log.append('import done')

# ---------- 2. 删除 tts_btn 块 ----------
start = line_index(lambda l: 'tts_btn' in l and 'QPushButton' in l)
assert start >= 0, 'tts_btn start not found'
end = line_index(lambda l: 'bottom.addWidget(self.tts_btn)' in l, start)
assert end >= 0, 'tts_btn block end not found'
del lines[start:end + 1]
log.append('tts_btn block removed (%d lines)' % (end - start + 1))

# ---------- 3. 删除 _open_tts_panel ----------
start = line_index(lambda l: bare(l).startswith('    def _open_tts_panel(self):'))
assert start >= 0, '_open_tts_panel not found'
end = start + 1
while end < len(lines):
    b = bare(lines[end])
    if b.startswith('    def ') or b.startswith('    class '):
        break
    if b.strip() and not b.startswith('    '):
        break
    end += 1
del lines[start:end]
log.append('_open_tts_panel removed')

# ---------- 4. _line_icon 增加 mic 分支 ----------
icon = line_index(lambda l: bare(l) == 'def _line_icon(kind: str, size: int = 18, color: str = TEXT_DIM) -> QIcon:')
assert icon >= 0, '_line_icon not found'
p_end = line_index(lambda l: bare(l) == '    p.end()', icon)
assert p_end >= 0, 'p.end() not found'
mic = (
    '    elif kind == "mic":       # 麦克风（语音合成音色）\n'
    '        p.drawRoundedRect(QRectF(s * 0.38, s * 0.14, s * 0.24, s * 0.44), s * 0.06, s * 0.06)\n'
    '        p.drawLine(QPointF(s * 0.38, s * 0.52), QPointF(s * 0.62, s * 0.52))\n'
    '        p.drawLine(QPointF(s * 0.38, s * 0.72), QPointF(s * 0.62, s * 0.72))\n'
    '        p.drawLine(QPointF(s * 0.50, s * 0.52), QPointF(s * 0.50, s * 0.72))\n'
    '        p.drawArc(QRectF(s * 0.34, s * 0.54, s * 0.32, s * 0.30), 0, 180 * 16)\n'
)
lines.insert(p_end, mic)
log.append('mic icon added')

# ---------- 5. nav 增加"语音合成" ----------
nav = line_index(lambda l: '("MCP 服务器", "server"),' in l)
assert nav >= 0, 'nav MCP not found'
lines.insert(nav + 1, '            ("语音合成", "mic"),\n')
log.append('nav item added')

# ---------- 6. stack 增加 _build_tts_page ----------
stk = line_index(lambda l: 'self._build_mcp_page()):' in l)
assert stk >= 0, 'stack page list not found'
lines[stk] = bare(lines[stk]).replace(
    'self._build_mcp_page()):',
    'self._build_mcp_page(),\n                     self._build_tts_page()):') + lines[stk][len(bare(lines[stk])):]
log.append('stack page added')

# ---------- 7. _build_tts_page 方法（插到 _build_skill_page 前）----------
skill = line_index(lambda l: bare(l).startswith('    def _build_skill_page('))
assert skill >= 0, '_build_skill_page not found'
tts_page = (
    '    def _build_tts_page(self) -> QWidget:\n'
    '        w = self._page("语音合成")\n'
    '        lay = self._page_body(w)\n'
    '        tip = QLabel("音色选择：AI 使用 tts_speak 调用 DashScope API 流式合成语音，"\n'
    '                     "边生成边自动播放，结果保存到工作目录 tts_output/。点击刷新从云端同步已创建的音色。")\n'
    '        tip.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")\n'
    '        tip.setWordWrap(True)\n'
    '        lay.addWidget(tip)\n'
    '        row = QHBoxLayout()\n'
    '        row.setSpacing(10)\n'
    '        lbl = QLabel("音色")\n'
    '        lbl.setStyleSheet(f"color: {self._TEXT}; font-size: 13px;")\n'
    '        lbl.setFixedWidth(70)\n'
    '        row.addWidget(lbl)\n'
    '        self.voice_combo = QComboBox()\n'
    '        self.voice_combo.currentIndexChanged.connect(self._on_voice_changed)\n'
    '        row.addWidget(self.voice_combo, 1)\n'
    '        refresh = QPushButton(_line_icon("net", 16), "刷新")\n'
    '        refresh.setStyleSheet(f"background: {self._PANEL}; color: {self._TEXT};"\n'
    '                             f"border: 1px solid {self._BORDER}; border-radius: 8px;"\n'
    '                             "padding: 6px 14px; font-weight: 600;")\n'
    '        refresh.setAutoDefault(False)\n'
    '        refresh.clicked.connect(self._reload_voices)\n'
    '        row.addWidget(refresh)\n'
    '        lay.addLayout(row)\n'
    '        self.voice_status = QLabel("")\n'
    '        self.voice_status.setStyleSheet(f"color: {self._DIM}; font-size: 12px;")\n'
    '        self.voice_status.setWordWrap(True)\n'
    '        lay.addWidget(self.voice_status)\n'
    '        lay.addStretch(1)\n'
    '        self._reload_voices()\n'
    '        return w\n'
    '\n'
    '    def _reload_voices(self):\n'
    '        """从 tts.json 与云端（list_voices）加载音色列表并选中当前音色"""\n'
    '        cfg = agent_tts.load_config()\n'
    '        current = str(cfg.get("voice_id", "")).strip()\n'
    '        known = {}\n'
    '        try:\n'
    '            for v in agent_tts.list_voices():\n'
    '                vid = str(v.get("voice", "")).strip()\n'
    '                if vid:\n'
    '                    known[vid] = str(v.get("gmt_create", ""))[:10]\n'
    '            self.voice_status.setText(f"云端音色 {len(known)} 个")\n'
    '        except Exception as e:\n'
    '            self.voice_status.setText(f"云端刷新失败（使用本地记录）：{e}")\n'
    '        if current not in known and current:\n'
    '            known[current] = "本地记录"\n'
    '        self.voice_combo.blockSignals(True)\n'
    '        self.voice_combo.clear()\n'
    '        for vid, date in known.items():\n'
    '            label = (str(cfg.get("preferred_name", "")) + " ") if vid == current else ""\n'
    '            self.voice_combo.addItem(f"{label}{vid[-12:]}（{date}）", vid)\n'
    '        if current:\n'
    '            idx = self.voice_combo.findData(current)\n'
    '            self.voice_combo.setCurrentIndex(idx if idx >= 0 else 0)\n'
    '        self.voice_combo.blockSignals(False)\n'
    '\n'
    '    def _on_voice_changed(self, _idx):\n'
    '        """选择音色立即写入 tts.json，AI 后续 tts_speak 无需再带 voice_id"""\n'
    '        vid = str(self.voice_combo.currentData() or "").strip()\n'
    '        if not vid:\n'
    '            return\n'
    '        if agent_tts.save_config(voice_id=vid):\n'
    '            self.voice_status.setText(f"已选用音色：{vid}")\n'
    '        else:\n'
    '            self.voice_status.setText("音色保存失败（无写入权限）")\n'
    '\n'
    '    def _build_skill_page('
)
lines[skill] = tts_page + lines[skill][len('    def _build_skill_page('):]
log.append('_build_tts_page added')

# ---------- 8. _save 末尾同步音色 ----------
acc = line_index(lambda l: bare(l).startswith('            self.accept()'))
assert acc >= 0, 'self.accept() not found'
lines.insert(acc, '            # 音色选择：独立写入 tts.json，避免被 settings.json 覆写\n'
                  '            vid = str(getattr(self, "voice_combo", None).currentData() or "").strip() if hasattr(self, "voice_combo") else ""\n'
                  '            if vid:\n'
                  '                agent_tts.save_config(voice_id=vid)\n')
log.append('_save tts sync added')

open(P, 'w', encoding='utf-8', newline='').write(''.join(lines))
sys.stdout.write('\n'.join(log) + '\nDONE\n')
sys.stdout.flush()
