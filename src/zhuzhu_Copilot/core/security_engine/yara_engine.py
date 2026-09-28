"""YARA 规则引擎：编译 config/yara/*.yar 规则，对文件/字节做恶意特征匹配。

- 规则目录可配置，规则文件全部外置（零硬编码）
- yara-python 不可用或编译失败时降级为禁用状态（enabled=False），不影响其他防护模块
- 匹配结果返回命中规则名列表，供编排器聚合告警
"""
import sys
from pathlib import Path
from typing import List, Optional

logger = __import__("zhuzhu_Copilot.utils.helpers", fromlist=["setup_logging"]).setup_logging()

try:
    import yara  # type: ignore
    _YARA_AVAILABLE = True
except Exception:  # pragma: no cover - 依赖缺失降级
    yara = None
    _YARA_AVAILABLE = False

from zhuzhu_Copilot.core.security_engine.config import config


def _resolve_rules_dir(raw: str) -> Path:
    """相对路径按项目根解析；打包后按 _MEIPASS 解析；绝对路径原样返回"""
    p = Path(raw)
    if p.is_absolute():
        return p
    base = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parents[4]
    return base / p


class YaraEngine:
    """YARA 规则匹配器：match_file / match_data 返回命中规则名列表"""

    def __init__(self):
        self.enabled = False
        self.rules_dir: Optional[Path] = None
        self._rules = None
        self._load()

    def _load(self) -> None:
        if not _YARA_AVAILABLE:
            logger.warning("yara-python 不可用，YARA 引擎已禁用")
            return
        if not config.enabled("yara"):
            return
        rules_dir = _resolve_rules_dir(str(config.get("yara.rules_dir", "config/yara")))
        files = sorted({p for p in rules_dir.glob("*.yar") if p.is_file()}
                       | {p for p in rules_dir.glob("*.yara") if p.is_file()})
        if not files:
            logger.warning("未找到 YARA 规则文件: %s", rules_dir)
            return
        try:
            # 每个规则文件独立命名空间（文件名），避免规则名冲突且便于定位来源
            sources = {p.stem: p.read_text(encoding="utf-8") for p in files}
            self._rules = yara.compile(sources=sources)
            self.enabled = True
            self.rules_dir = rules_dir
            logger.info("YARA 引擎已加载 %d 个规则文件", len(files))
        except Exception as e:
            logger.warning("YARA 规则编译失败: %s", e)
            self._rules = None

    def match_file(self, path: str) -> List[str]:
        if not self._rules:
            return []
        try:
            return [m.rule for m in self._rules.match(path)]
        except Exception:
            return []

    def match_data(self, data: bytes) -> List[str]:
        if not self._rules:
            return []
        try:
            return [m.rule for m in self._rules.match(data=data)]
        except Exception:
            return []


yara_engine = YaraEngine()
