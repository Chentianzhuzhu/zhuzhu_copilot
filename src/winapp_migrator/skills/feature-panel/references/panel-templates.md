# 功能面板模板（feature-panel 附册）

> AI 生成面板前先读本文件：在下方三个模板基础上改，禁止凭空造轮子、禁止 mock。
> 面板运行在程序进程内：可 `from PyQt6.QtWidgets import ...`；读写文件用标准库；
> 调用 AI 能力（读/写文件、执行命令）可 `from winapp_migrator.core import agent_tools`。
> 需要第三方库（如 paramiko）时：`set_feature_deps` 声明 → 界面里对 ImportError 给出"未安装"提示。

## 公共片段（每个面板都可带）

```python
# -*- coding: utf-8 -*-
TITLE = "面板标题"
WIDTH = 360
HEIGHT = 420
BORDER = "#D6DCE5"
BG = "#1A1A1E"
TEXT = "#F2F4F8"
ACCENT = "#1F3864"
DIMMED = "#9AA3AE"

def _qss():
    return (f"QLineEdit,QPlainTextEdit,QListWidget {{ background:{BG}; color:{TEXT};"
            f"border:1px solid {BORDER}; border-radius:6px; padding:4px; }}"
            f"QPushButton {{ background:{ACCENT}; color:#FFFFFF; border:none;"
            f"border-radius:6px; padding:6px 12px; font-weight:600; }}"
            f"QPushButton:hover {{ background:#2A4A8A; }}")
```

## 模板 1：文件编辑器面板（打开/编辑/保存任意文件）

```python
# -*- coding: utf-8 -*-
TITLE = "文件编辑器"
WIDTH = 380
HEIGHT = 440

def build_panel(owner):
    from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
                                 QPushButton, QLineEdit, QFileDialog, QMessageBox)
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(10, 8, 10, 8)
    bar = QHBoxLayout()
    path_edit = QLineEdit()
    path_edit.setPlaceholderText("文件绝对路径，如 C:\\repo\\a.py")
    bar.addWidget(path_edit, 1)
    btn_open = QPushButton("打开")
    btn_open.setToolTip("按路径读取文件到编辑区")
    btn_save = QPushButton("保存")
    btn_save.setToolTip("将编辑区内容写回文件（自动备份到 .bak）")
    bar.addWidget(btn_open)
    bar.addWidget(btn_save)
    lay.addLayout(bar)
    editor = QPlainTextEdit()
    lay.addWidget(editor, 1)
    status = QPushButton("当前工作目录")
    status.setStyleSheet("text-align:left; background:transparent; color:#9AA3AE; border:none;")
    lay.addWidget(status)

    def _load(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            editor.setPlainText(f.read())
        status.setText(f"{len(editor.toPlainText())} 字符  @ {path}")

    def on_open():
        path = path_edit.text().strip()
        if not path:
            d = QFileDialog.getOpenFileName(box, "选择要编辑的文件")[0]
            if not d:
                return
            path = d
        try:
            _load(path)
            path_edit.setText(path)
        except OSError as e:
            QMessageBox.warning(box, "打开失败", f"{e}")

    def on_save():
        path = path_edit.text().strip()
        if not path:
            QMessageBox.information(box, "提示", "请先填写/打开文件路径")
            return
        try:
            import shutil
            if os.path.exists(path):
                shutil.copyfile(path, path + ".bak")
            with open(path, "w", encoding="utf-8") as f:
                f.write(editor.toPlainText())
            status.setText(f"已保存 @ {path}（备份 {path}.bak）")
        except OSError as e:
            QMessageBox.warning(box, "保存失败", f"{e}")

    import os
    btn_open.clicked.connect(on_open)
    btn_save.clicked.connect(on_save)
    try:
        from winapp_migrator.core import agent_tools
        wd = agent_tools.get_workdir()
        if not wd:
            raise OSError("no workdir")
        path_edit.setText(os.path.join(wd, "README.md"))
        if os.path.isfile(os.path.join(wd, "README.md")):
            _load(os.path.join(wd, "README.md"))
        else:
            status.setText(f"工作目录：{wd}")
    except Exception:
        status.setText("未设置工作目录")
    return box
```

## 模板 2：PPT 演示面板（打开 .pptx → 逐页演示文字内容）

```python
# -*- coding: utf-8 -*-
import os
import html
import re
import zipfile

TITLE = "PPT 演示"
WIDTH = 380
HEIGHT = 460


def _slide_texts(path):
    """读取 pptx 各页文字（<a:t>），返回 [页文本, ...]；无额外依赖。"""
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist()
                 if n.startswith("ppt/slides/slide") and n.endswith(".xml")]
        names.sort(key=lambda n: int(re.search(r"slide(\d+)\\.xml", n).group(1)))
        out = []
        for n in names:
            xml = z.read(n).decode("utf-8", "ignore")
            txts = re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)
            out.append("\n".join(html.unescape(t) for t in txts if t.strip()))
    return out


def build_panel(owner):
    from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                 QPushButton, QFileDialog, QMessageBox)
    from PyQt6.QtCore import Qt as _Qt
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(10, 8, 10, 8)
    bar = QHBoxLayout()
    btn_open = QPushButton("打开 PPT")
    btn_prev = QPushButton("上一页")
    btn_next = QPushButton("下一页")
    for b in (btn_prev, btn_next):
        b.setEnabled(False)
    bar.addWidget(btn_open)
    bar.addWidget(btn_prev)
    bar.addWidget(btn_next)
    lay.addLayout(bar)
    viewer = QLabel("请打开一个 .pptx 文件开始演示")
    viewer.setStyleSheet("background:#F5F7FA; border:1px solid #D6DCE5;"
                         "border-radius:8px; padding:10px; font-size:15px;")
    viewer.setWordWrap(True)
    viewer.setAlignment(_Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(viewer, 1)
    page = QLabel("0 / 0")
    page.setStyleSheet("color:#9AA3AE;")
    lay.addWidget(page)

    slides, idx = [], -1

    def _show():
        nonlocal idx
        if not slides:
            return
        txt = slides[idx]
        safe = txt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        viewer.setText(f"<b>第 {idx + 1} 页 / 共 {len(slides)} 页</b><br><br>{safe.replace(chr(10), '<br>')}")
        page.setText(f"{idx + 1} / {len(slides)}")

    def on_open():
        nonlocal slides, idx
        path, _ = QFileDialog.getOpenFileName(box, "选择 PPT 演示文稿", "", "PPT (*.pptx)")
        if not path:
            return
        try:
            slides = _slide_texts(path)
            if not slides:
                QMessageBox.information(box, "提示", "该文件未解析到任何页面文字")
                return
            idx = 0
            btn_prev.setEnabled(True)
            btn_next.setEnabled(True)
            _show()
        except Exception as e:
            QMessageBox.warning(box, "打开失败", f"{e}")

    def on_prev():
        nonlocal idx
        if slides and idx > 0:
            idx -= 1
            _show()

    def on_next():
        nonlocal idx
        if slides and idx < len(slides) - 1:
            idx += 1
            _show()

    btn_open.clicked.connect(on_open)
    btn_prev.clicked.connect(on_prev)
    btn_next.clicked.connect(on_next)
    return box
```

## 模板 3：SSH 连接服务器面板（paramiko，后台线程，不存密码）

```python
# -*- coding: utf-8 -*-
TITLE = "SSH 连接服务器"
WIDTH = 400
HEIGHT = 460


def build_panel(owner):
    from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                                 QLineEdit, QPlainTextEdit, QPushButton)
    from PyQt6.QtCore import QThread, pyqtSignal
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(10, 8, 10, 8)

    form = QVBoxLayout()
    form.setSpacing(4)
    host = QLineEdit()
    host.setPlaceholderText("主机地址，如 192.168.1.10")
    user = QLineEdit()
    user.setPlaceholderText("用户名")
    pwd = QLineEdit()
    pwd.setEchoMode(QLineEdit.EchoMode.Password)
    pwd.setPlaceholderText("密码（仅本次会话使用，不落盘）")
    port = QLineEdit("22")
    port.setFixedWidth(64)
    row = QHBoxLayout()
    row.addWidget(host)
    row.addWidget(port)
    form.addLayout(row)
    form.addWidget(user)
    form.addWidget(pwd)
    lay.addLayout(form)

    bar = QHBoxLayout()
    btn_conn = QPushButton("连接")
    btn_run = QPushButton("执行命令")
    btn_run.setEnabled(False)
    bar.addWidget(btn_conn)
    bar.addWidget(btn_run)
    lay.addLayout(bar)

    cmd = QLineEdit()
    cmd.setPlaceholderText("输入命令，如 ls -la")
    lay.addWidget(cmd)
    out = QPlainTextEdit()
    out.setReadOnly(True)
    lay.addWidget(out, 1)

    state = {"client": None}

    class _Worker(QThread):
        finished_ok = pyqtSignal(str)
        failed = pyqtSignal(str)

        def __init__(self, target, args=()):
            super().__init__()
            self._target, self._args = target, args

        def run(self):
            try:
                self.finished_ok.emit(self._target(*self._args))
            except Exception as e:
                self.failed.emit(repr(e))

    def _connect_task(h, p, u, pw):
        try:
            import paramiko
        except ImportError:
            return "未安装 paramiko：请让 AI 对当前工作流执行 set_feature_deps 声明 paramiko 后重载本面板"
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(hostname=h, port=int(p), username=u, password=pw, timeout=10)
        state["client"] = c
        return f"已连接 {u}@{h}:{p}"

    def _exec_task(cmd_txt):
        c = state.get("client")
        if c is None:
            return "未连接"
        _, stdout, stderr = c.exec_command(cmd_txt, timeout=30)
        return stdout.read().decode("utf-8", "replace") + stderr.read().decode("utf-8", "replace")

    def _wrap(fn, *args):
        def _on_ok(msg):
            out.appendPlainText(str(msg))
            btn_run.setEnabled(state.get("client") is not None)
        def _on_err(e):
            out.appendPlainText("错误: " + str(e))
        w = _Worker(fn, args)
        w.finished_ok.connect(_on_ok)
        w.failed.connect(_on_err)
        w.start()
        out.appendPlainText("执行中…")

    def on_conn():
        if state.get("client") is not None:
            try:
                state["client"].close()
            except Exception:
                pass
            state["client"] = None
            btn_conn.setText("连接")
            btn_run.setEnabled(False)
            out.appendPlainText("已断开")
            return
        if not (host.text().strip() and user.text().strip() and pwd.text()):
            out.appendPlainText("请填写主机/用户名/密码")
            return
        btn_conn.setText("断开")
        _wrap(_connect_task, host.text().strip(), port.text().strip() or "22",
              user.text().strip(), pwd.text())

    def on_run():
        if not cmd.text().strip():
            return
        _wrap(_exec_task, cmd.text().strip())

    btn_conn.clicked.connect(on_conn)
    btn_run.clicked.connect(on_run)
    return box
```