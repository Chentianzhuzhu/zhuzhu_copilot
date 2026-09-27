"""统一安全引擎编排器：聚合各防护模块，提供统一 start/stop/tick 接口。

- tick() 一轮巡检：蜜罐勒索 / 自启动 / DNS / 弹窗记录 / 下载目录文件，返回聚合结果
- scan_file() 对单个文件做 YARA 恶意特征扫描（供进程/文件分析调用）
- analyze_file()/analyze_process()/analyze_script() 深度分析入口（规则+YARA+可选LLM）
- 各模块开关由配置 enabled_modules 控制，禁用模块跳过
"""
from typing import List

from winapp_migrator.core.security_engine.config import config
from winapp_migrator.core.security_engine.deep_analyze import deep_analyzer
from winapp_migrator.core.security_engine.dns_guard import dns_guard
from winapp_migrator.core.security_engine.download_guard import download_guard
from winapp_migrator.core.security_engine.popup_guard import popup_guard
from winapp_migrator.core.security_engine.ransomware_guard import ransomware_guard
from winapp_migrator.core.security_engine.startup_guard import startup_guard
from winapp_migrator.core.security_engine.yara_engine import yara_engine


class SecurityEngine:
    """防护模块编排器（门面）"""

    def start(self) -> None:
        """启动需要后台线程的模块（弹窗拦截）"""
        if config.enabled("popup"):
            popup_guard.start()

    def stop(self) -> None:
        popup_guard.stop()

    def tick(self) -> dict:
        """一轮巡检，返回 {'ransomware','startup','dns','popup','download'} 聚合结果"""
        result: dict = {}
        if config.enabled("ransomware"):
            result["ransomware"] = ransomware_guard.check()
        if config.enabled("startup"):
            result["startup"] = startup_guard.check()
        if config.enabled("dns"):
            result["dns"] = dns_guard.check()
        if config.enabled("download"):
            result["download"] = download_guard.check()
        result["popup"] = popup_guard.drain()
        return result

    def scan_file(self, path: str) -> List[str]:
        """YARA 扫描单个文件，返回命中规则名列表（引擎禁用时为空）"""
        return yara_engine.match_file(path) if yara_engine.enabled else []

    def analyze_file(self, path: str) -> dict:
        """深度分析单个文件：静态特征 + 规则/YARA + 可选 LLM，返回判定结果"""
        return deep_analyzer.analyze_file(path)

    def analyze_process(self, proc: dict) -> dict:
        """深度分析单个进程（pid/name/path）：进程文件静态特征 + 规则/YARA + 可选 LLM"""
        return deep_analyzer.analyze_process(proc)

    def analyze_script(self, script: str, path: str = "", name: str = "") -> dict:
        """深度分析脚本内容：文本特征规则 + 可选 LLM"""
        return deep_analyzer.analyze_script(script, path, name)

    @property
    def yara_available(self) -> bool:
        return yara_engine.enabled


engine = SecurityEngine()
