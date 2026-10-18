"""上下文图片体积治理回归测试（修复「AI 操作电脑插件几步上下文就爆」）。

根因（两条叠加）：
1. 截图以**未压缩原图** data URL 写入消息历史（tool 图片 + 每轮视觉 user 消息），
   1280 宽 PNG 桌面截图 base64 常达 1~3 MB 字符；
2. `estimate_image_tokens()` 无参、恒返回 765 —— 本地占用估算比真实输入小几百倍，
   压缩阈值（窗口 × 0.75）永不触发 → 请求直接越窗（上游 400 / 上下文爆满）。

本文件覆盖修复后的行为：
- `estimate_image_tokens(url)` 按 URL 长度做保守下界校准，且保留无参兼容（765）；
- `_context_image` 把任意大图压到字节硬上限内（限宽 + JPEG + 逐级降质/降宽）；
- 写入 tool 消息与下一轮视觉消息的截图都是**治理后**的那一份；
- `_prune_images` 既剥离超量旧图，也对留存的旧原图补治理（老会话遗留场景）；
- `_hard_trim` 增量实现在保留首个 system、不切断 tool 配对的前提下仍能压到 limit 内。
"""
import base64
import os
import random
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from zhuzhu_Copilot.core import agent_engine, agent_llm


class _StubLLM:
    model = "ctx-image-test"

    def chat_stream(self, messages, stop=None, **_kw):
        return {"text": "## 任务目标\n测试"}


def _engine(text_only=False):
    return agent_engine.AgentEngine(_StubLLM(), text_only=text_only)


def _noisy_png(width=1024, height=576, seed=7) -> str:
    """生成一张"内容丰富"的 PNG data URL（模拟真实桌面截图的体积量级）。

    真实桌面截图（1280×720 PNG）通常 300 KB ~ 1.5 MB；这里用随机像素保证 PNG
    不可压缩，得到同量级样本，才能真实验证治理与估算口径（纯色块会被 PNG 压到
    十几 KB，样本失真会让用例失去意义）。
    """
    rnd = random.Random(seed)
    raw = rnd.randbytes(width * height * 4)
    img = QImage(raw, width, height, width * 4,
                 QImage.Format.Format_RGB32).copy()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(bytes(ba)).decode()


def _jpeg_supported() -> bool:
    """运行环境是否带 JPEG 编码器（缺少时治理会原样回退，相关断言跳过）。"""
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    return bool(img.save(buf, "JPEG", 80))


# ---------- 1. 图片 token 估算不再恒定 ----------

def test_image_tokens_without_url_keeps_legacy_flat_value():
    """无 URL 调用保持旧行为（UI 兜底/向后兼容）"""
    assert agent_llm.estimate_image_tokens() == 765


def test_image_tokens_scales_with_url_size():
    """带 URL 时按长度保守上调 —— 这是"压缩阈值永不触发"的修复点"""
    small = agent_llm.estimate_image_tokens("data:image/png;base64," + "A" * 1000)
    big = agent_llm.estimate_image_tokens("data:image/png;base64," + "A" * 1_200_000)
    assert small == 765, "小图不应被高估"
    assert big >= 60_000, f"1.2MB 字符的图必须显著计入，实际 {big}"
    assert big > small * 50, (small, big)


def test_image_tokens_monotonic_and_capped():
    """单调不减且有上限（防单张图把预算判定完全架空）"""
    vals = [agent_llm.estimate_image_tokens("x" * n) for n in (1000, 10_000, 100_000, 5_000_000)]
    assert vals == sorted(vals), vals
    assert vals[-1] == agent_llm._IMAGE_TOKENS_CAP, vals


# ---------- 2. 进入上下文的图片必须过体积治理 ----------

def test_context_image_shrinks_raw_screenshot():
    """未压缩原图 → 治理后 base64 显著变小且不超硬上限"""
    if not _jpeg_supported():
        import pytest
        pytest.skip("运行环境缺少 JPEG 编码器")
    raw = _noisy_png()
    assert len(raw) > agent_engine._CTX_IMAGE_MAX_B64, "样本图应大于硬上限，否则用例无意义"
    slim = agent_engine._context_image(raw)
    assert slim.startswith("data:image/jpeg;base64,")
    assert len(slim) <= agent_engine._CTX_IMAGE_MAX_B64
    assert len(slim) < len(raw) / 2, "治理后体积应至少减半"


def test_context_image_passes_through_non_image():
    """非图片输入原样返回，不抛异常"""
    assert agent_engine._context_image("data:text/plain;base64,AA") == "data:text/plain;base64,AA"
    assert agent_engine._context_image("") == ""


def test_context_image_survives_garbage_base64():
    """坏数据不炸（回退原值），保证工具链不因图片治理中断"""
    bad = "data:image/png;base64,!!!not-base64!!!"
    assert agent_engine._context_image(bad) == bad


# ---------- 3. 历史修剪：剥离超量旧图 + 治理留存旧图 ----------

def _tool_img_msg(url: str, i: int) -> dict:
    return {"role": "tool", "tool_call_id": f"c{i}",
            "content": [{"type": "text", "text": "已截屏"}, 
                        {"type": "image_url", "image_url": {"url": url, "detail": "high"}}]}


def test_prune_images_drops_old_and_slims_kept():
    """超量旧图剥离为占位文本；留存的那张若是旧原图则被顺带治理"""
    if not _jpeg_supported():
        import pytest
        pytest.skip("运行环境缺少 JPEG 编码器")
    big = _noisy_png()
    eng = _engine()
    eng._messages = [{"role": "system", "content": "sys"}]
    for i in range(4):
        eng._messages.append(_tool_img_msg(big, i))

    dropped = eng._prune_images()
    assert dropped == 2, "4 张图只保留最近 2 张，应剥离 2 张"

    urls = []
    for m in eng._messages:
        c = m.get("content")
        if isinstance(c, list):
            for x in c:
                if isinstance(x, dict) and x.get("type") == "image_url":
                    urls.append(x["image_url"]["url"])
    assert len(urls) == 2
    assert all(len(u) <= agent_engine._CTX_IMAGE_MAX_B64 for u in urls)
    # 被剥离的纯图片消息不再留下空 content 数组
    for m in eng._messages:
        if isinstance(m.get("content"), list):
            assert m["content"], "空 content 数组会被上游拒绝"


def test_prune_images_placeholder_for_pure_image_message():
    """纯图片消息被剥离后补占位文本，而不是留空数组"""
    eng = _engine()
    eng._messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "image_url",
                                      "image_url": {"url": "data:image/png;base64,AA"}}]},
    ]
    eng._prune_images(max_keep=0)
    assert eng._messages[1]["content"] == [{"type": "text", "text": "（截图已忽略）"}]


# ---------- 4. 估算口径把图片真实计入（集成视角） ----------

def test_msg_estimate_counts_image_url_length():
    """同一条图片消息：原图比治理图占用高得多 —— 阈值判定能感知差异"""
    raw = _noisy_png()
    eng = _engine()
    m_raw = _tool_img_msg(raw, 0)
    est_raw = eng._msg_estimate(m_raw)
    if not _jpeg_supported():
        import pytest
        pytest.skip("运行环境缺少 JPEG 编码器")
    m_slim = _tool_img_msg(agent_engine._context_image(raw), 0)
    est_slim = eng._msg_estimate(m_slim)
    assert est_raw > est_slim * 2, (est_raw, est_slim)


def test_few_steps_of_screenshots_stay_far_below_window():
    """回归主诉：连续几步截图操作不再让上下文爆掉。

    10 步 × 每步一条截图 tool 消息 + 末尾视觉消息，治理后总估算必须远低于 1M 窗口
    （旧实现：10 张原图 ≈ 数 MB 字符，网关按文本计即直接越窗）。"""
    raw = _noisy_png()
    eng = _engine()
    eng._messages = [{"role": "system", "content": "系统提示"}]
    for i in range(10):
        eng._messages.append(_tool_img_msg(raw, i))
        eng._messages.append({"role": "user", "content": [
            {"type": "text", "text": "请观察最新截图并继续。"},
            {"type": "image_url", "image_url": {"url": raw, "detail": "high"}}]})
    eng._prune_images()
    est = eng._estimate_raw()
    assert est < 200_000, f"治理后 10 步截图占用仍过高：{est}"


# ---------- 5. 硬裁增量实现行为不变 ----------

def test_hard_trim_incremental_keeps_system_and_pairing():
    """增量版 _hard_trim：保留首个 system、tool 配对完整、估算降到 limit 以内"""
    eng = _engine()
    eng._messages = [{"role": "system", "content": "SYS"}]
    for i in range(30):
        eng._messages.append({"role": "assistant", "content": "",
                              "tool_calls": [{"id": f"c{i}", "type": "function",
                                              "function": {"name": "run_command",
                                                           "arguments": '{"command":"echo %s"}' % ("x" * 300)}}]})
        eng._messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "结果" * 500})

    limit = eng._input_budget() // 6
    eng._hard_trim(limit)
    assert eng._messages[0]["role"] == "system"
    assert eng._estimate_tokens() <= limit or len(eng._messages) <= 2
    for i, m in enumerate(eng._messages):
        if m.get("role") == "tool":
            prev = eng._messages[i - 1]
            assert prev.get("role") == "assistant" and prev.get("tool_calls")
