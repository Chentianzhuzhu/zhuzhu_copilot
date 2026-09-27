"""下载目录防护测试：增量扫描 / 脚本内容分析 / 节流 / 隔离动作"""
from winapp_migrator.core.security_engine.config import config
from winapp_migrator.core.security_engine.download_guard import DownloadGuard


def _make_guard(tmp_path, monkeypatch, action="notify", interval=1.0):
    monkeypatch.setattr("winapp_migrator.core.security_engine.download_guard.scan_dirs",
                        lambda: [str(tmp_path)])
    dl = config.data.setdefault("download_guard", {})
    monkeypatch.setitem(dl, "scan_interval_s", interval)
    monkeypatch.setitem(dl, "action", action)
    # 测试期内禁用 LLM 二次判定：规则判定即确定性结果，不发起真实网络请求
    monkeypatch.setattr("winapp_migrator.core.security_engine.llm_analyzer.resolve_cfg",
                        lambda: None)
    return DownloadGuard()


def test_new_malicious_script_detected(tmp_path, monkeypatch):
    g = _make_guard(tmp_path, monkeypatch)
    (tmp_path / "evil.bat").write_text(
        "powershell -enc " + "A" * 120)
    (tmp_path / "ok.txt").write_text("hello")
    records = g.check()
    assert len(records) == 1
    rec = records[0]
    assert rec["verdict"] == "malicious"
    assert rec["path"].endswith("evil.bat")
    assert rec.get("signals")


def test_incremental_skip_unchanged(tmp_path, monkeypatch):
    g = _make_guard(tmp_path, monkeypatch, interval=0)   # 不节流（专注验证快照去重）
    (tmp_path / "evil.bat").write_text("powershell -enc " + "A" * 120)
    assert len(g.check()) == 1
    assert g.check() == []          # 快照命中：未变更不再重复分析
    (tmp_path / "evil.bat").write_text("powershell -enc " + "B" * 300)   # 变更 → 重新分析
    assert len(g.check()) == 1


def test_throttle_interval(tmp_path, monkeypatch):
    g = _make_guard(tmp_path, monkeypatch, interval=60)
    (tmp_path / "evil.bat").write_text("powershell -enc " + "A" * 120)
    assert len(g.check()) == 1
    assert g.check() == []   # 60s 节流窗口内不再扫描


def test_quarantine_action(tmp_path, monkeypatch):
    g = _make_guard(tmp_path, monkeypatch, action="quarantine")
    evil = tmp_path / "evil.bat"
    evil.write_text("powershell -enc " + "A" * 120)
    from winapp_migrator.core.security_engine.quarantine import quarantine
    monkeypatch.setattr(quarantine, "files_dir", tmp_path / "q" / "files")
    monkeypatch.setattr(quarantine, "meta_dir", tmp_path / "q" / "meta")
    records = g.check()
    assert len(records) == 1 and records[0]["isolated"] is True
    assert not evil.exists()   # 已移入隔离区


def test_analyze_file_single(tmp_path, monkeypatch):
    g = _make_guard(tmp_path, monkeypatch)
    evil = tmp_path / "x.vbs"
    evil.write_text('powershell -enc ' + "A9" * 60)   # 编码命令 → 高置信恶意
    rec = g.analyze_file(str(evil))
    assert rec is not None and rec["verdict"] in ("malicious", "suspicious")
    assert g.analyze_file(str(tmp_path / "missing")) is None