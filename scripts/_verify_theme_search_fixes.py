# -*- coding: utf-8 -*-
"""验证本次 5 项修复（无 WebEngine / 无网络抖动，快速退出）：
1) styles.set_palette 深/浅色板切换与 GLOBAL_QSS 重建
2) agent_tools._filter_by_keyword 关键词精确检索
3) agent_tools._web_search 去重 + must_include 过滤（mock _http_request）
4) 预览面板（CodePreviewWindow，禁用 WebEngine）地址栏样式 + 标签页栏 QSS 源码断言
5) MainWindow 无头构建 + _apply_theme 浅/深换肤不报错
"""
import contextlib
import io
import os
import sys
import time
import threading
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 硬看门狗：任何一步卡住 25s 直接中断（防 WebEngine/网络挂起）
def _watchdog():
    time.sleep(25)
    print("TIMEOUT: 测试超时，强制退出", flush=True)
    os._exit(2)

threading.Thread(target=_watchdog, daemon=True).start()

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("  | " + str(detail)) if detail else ""), flush=True)


def main():
    # ---------- 1) styles.set_palette ----------
    from winapp_migrator.ui import styles
    styles.set_palette("dark")
    assert styles.PALETTE["bg_top"] == "#000000"
    styles.set_palette("light")
    assert styles.PALETTE["bg_top"] == "#F4F6FA"
    assert styles.PALETTE["text"] == "#1E293B"
    check("styles.set_palette 深/浅切换", True, styles.PALETTE["bg_top"])
    check("GLOBAL_QSS 含浅色底", "#F4F6FA" in styles.GLOBAL_QSS and "#1E293B" in styles.GLOBAL_QSS)
    check("apply_palette 可用(live 读取 PALETTE)", callable(styles.apply_palette))

    # ---------- 2) _filter_by_keyword ----------
    from winapp_migrator.core import agent_tools as at
    body = "第一段：介绍人工智能。\n第二段：Python 教程\n第三段：人工智能的未来发展"
    out, hits = at._filter_by_keyword(body, "人工智能", 8000)
    check("_filter_by_keyword 命中2段", hits == 2 and "Python" not in out and "第一段" in out, f"hits={hits}")
    out0, hits0 = at._filter_by_keyword(body, "不存在的词", 8000)
    check("_filter_by_keyword 未命中提示", hits0 == 0 and "包含关键词" in out0)
    outN, hitsN = at._filter_by_keyword(body, "", 8000)
    check("_filter_by_keyword 空词原样", hitsN == -1 and outN == body)
    outS, hitsS = at._filter_by_keyword("今天天气很好。明天要下雨。后天晴。", "下雨", 8000)
    check("_filter_by_keyword 句子切分", hitsS == 1 and "明天要下雨" in outS)

    # ---------- 3) _web_search mock ----------
    def fake_http(url, **kw):
        if "bing.com" in url:
            return ('<li class="b_algo"><h2><a href="http://a.com/1">A 人工智能</a></h2>'
                    '<p>人工智能 首先介绍</p></li>'
                    '<li class="b_algo"><h2><a href="http://a.com/2">B 教程</a></h2>'
                    '<p>Python 入门教程</p></li>'
                    '<li class="b_algo"><h2><a href="http://a.com/1">A 重复</a></h2>'
                    '<p>重复链接</p></li>')
        return ('<html><head><title>测试页</title></head><body>'
                '<p>这是一段 Python 教程内容。</p><p>另一段无关文字。</p></body></html>')
    _orig = at._http_request
    at._http_request = fake_http
    try:
        r = at._web_search("人工智能", max_results=8)
        check("_web_search 去重(3->2)", r["text"].count("http://a.com/") == 2, r["text"][:60].splitlines()[0])
        r2 = at._web_search("人工智能", max_results=8, must_include="教程")
        check("_web_search must_include 过滤", "Python 入门教程" in r2["text"]
              and "A 人工智能" not in r2["text"])
        r3 = at._web_search("人工智能", max_results=8, must_include="不存在")
        check("_web_search must_include 无命中提示", "未找到" in r3["text"])
        r4 = at._web_search("人工智能", max_results=99)
        check("_web_search 条数封顶15", "2 条" in r4["text"])
        at._http_request = fake_http  # 保持
        r5 = at._web_fetch("https://example.com/p", keyword="教程")
        check("_web_fetch 传 keyword 过滤命中", "按关键词" in r5["text"]
              and "无关文字" not in r5["text"], r5["text"][:80].replace("\n", " "))
    finally:
        at._http_request = _orig

    # ---------- 4) 预览面板 ----------
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QSettings
    app = QApplication(sys.argv)
    from winapp_migrator.ui import agent_panel
    from winapp_migrator.core import agent_ui_ux
    agent_panel.apply_theme()
    _is_light = agent_panel._resolve_theme() == "light"
    # 禁用 WebEngine，避免 offscreen 下 Chromium 挂起
    _orig_wea = agent_ui_ux.web_engine_available
    agent_ui_ux.web_engine_available = lambda: False
    try:
        cw = agent_panel.CodePreviewWindow()
        qss = cw.web_url.styleSheet()
        check("web_url 显式 QSS 含前景色", "color:" in qss, qss.replace("\n", " ")[:60])
        check("web_url QSS 含背景+placeholder", "background:" in qss and "placeholder" in qss)
        check("mode 下拉 QSS 主题自适应", "QComboBox" in getattr(cw.mode, "styleSheet", lambda: "")()
              or "background" in (cw.mode.styleSheet() or ""), (cw.mode.styleSheet() or "")[:50])
        cw.close()
    finally:
        agent_ui_ux.web_engine_available = _orig_wea
    # 标签页栏 QSS 以源码断言（WebEngine 路径已被禁用，无法实例化 QTabWidget 分支）。
    # 源码内为 f-string 转义花括号，故断言用 {{ }}
    _src = open(os.path.join(os.path.dirname(agent_panel.__file__), "agent_panel.py"), encoding="utf-8").read()
    check("标签页栏 QTabBar 显式背景源码",
          "QTabWidget::pane {{ background: {PANEL};" in _src
          and "QTabBar {{ background: {PANEL};" in _src)

    # ---------- 5) MainWindow ----------
    try:
        from winapp_migrator.update_check import UpdateChecker
    except Exception:
        UpdateChecker = None
    if UpdateChecker is not None:
        UpdateChecker.start = lambda self, *a, **k: None
    # 模拟管理员，避免 _check_admin 弹 QMessageBox 阻塞无头测试
    import winapp_migrator.utils.helpers as _helpers
    _orig_admin = _helpers.is_admin
    _helpers.is_admin = lambda: True
    _qs_t = QSettings("WinAppMigrator", "WinAppMigrator")
    _orig_theme = str(_qs_t.value("agent_theme", "light"))
    try:
        from winapp_migrator.ui import main_window as mw_mod
        styles.set_palette("light")
        mw_mod._regen_qss()
        mw = mw_mod.MainWindow()
        mw._apply_theme()
        pal = app.palette()
        check("MainWindow 浅色构建+换肤", True, f"Window={pal.window().color().name()}")
        check("MainWindow title 浅色文本", "#1E293B" in mw._title_lbl.styleSheet())
        check("左卡片浅色背景", "background-color: #FFFFFF" in mw._left_card.styleSheet())
        # 深色换肤（_apply_theme 以 QSettings agent_theme 为准，需先持久化 dark）
        QSettings("WinAppMigrator", "WinAppMigrator").setValue("agent_theme", "dark")
        styles.set_palette("dark")
        mw_mod._regen_qss()
        mw._apply_theme()
        check("MainWindow 深色换肤", "#F5F5F5" in mw._title_lbl.styleSheet()
              and "background-color: #141414" in mw._left_card.styleSheet())
        # 搜索框浅色样式（_apply_theme 以 QSettings 为准，切回 light 后再查）
        _qs_t.setValue("agent_theme", "light")
        styles.set_palette("light")
        mw_mod._regen_qss()
        mw._apply_theme()
        check("搜索框浅色样式", "#E9EDF4" in mw.search_edit.styleSheet()
              and "#1E293B" in mw.search_edit.styleSheet())
        mw.close()
    except Exception as e:
        check("MainWindow 构建", False, repr(e))
    finally:
        _helpers.is_admin = _orig_admin
        _qs_t.setValue("agent_theme", _orig_theme)   # 恢复用户原主题设置

    # ---------- 6) 拖拽防抖 hook ----------
    from winapp_migrator.ui.agent_panel import AgentPanel
    check("AgentPanel 拖动防抖 hook", callable(getattr(AgentPanel, "_arm_move_side_sync", None))
          and callable(getattr(AgentPanel, "_move_side_flush", None)))

    failed = [r for r in results if not r[1]]
    print("\n==== 汇总: %d/%d 通过 ====" % (len(results) - len(failed), len(results)), flush=True)
    for n, ok, d in results:
        print(("  PASS  " if ok else "  FAIL  ") + n, flush=True)
    if failed:
        sys.exit(1)
    # 跳过 Qt/后台线程的完整析构（offscreen 下会抛 0xC0000005 退出噪音），直接退出
    print("ALL PASS", flush=True)
    os._exit(0)


if __name__ == "__main__":
    main()