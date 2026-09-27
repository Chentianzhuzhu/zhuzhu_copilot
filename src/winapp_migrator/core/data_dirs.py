"""检测应用关联的数据目录（AppData/文档等安装目录之外的目录），供迁移时勾选"""
import os
import re
from pathlib import Path
from typing import List

from winapp_migrator.core.app_scanner import AppInfo

logger = __import__("winapp_migrator.utils.helpers", fromlist=["setup_logging"]).setup_logging()

# 常见别名：app 显示名/英文名 -> 可能的数据目录关键词
_ALIAS = {
    "微信": {"wechat", "weixin"},
    "weixin": {"wechat", "wechat files"},
    "wechat": {"weixin", "wechat files"},
    "qq": {"tencent files", "qq"},
    "腾讯": {"tencent", "tencent files", "qq"},
    "tim": {"tencent files", "tim"},
    "钉钉": {"dingtalk"},
    "dingtalk": {"dingtalk"},
    "网易云音乐": {"netease", "cloudmusic"},
    "office": {"microsoft office", "office"},
    "steam": {"steam"},
}
# 过于通用的分词，避免误匹配无关目录
_STOPWORDS = {
    "app", "apps", "data", "user", "users", "client", "file", "files",
    "config", "configs", "setting", "settings", "launcher", "tool", "tools",
    "program", "programs", "log", "logs", "cache", "temp", "common", "service",
}

def _tokens(app: AppInfo) -> set:
    """生成匹配关键词：名称/发布者/可执行文件名 + 别名，过滤停用词"""
    toks = set()
    for s in (app.name, app.publisher, Path(app.executable or "").stem):
        s = (s or "").lower()
        toks.update(re.findall(r"[a-z0-9\u4e00-\u9fff]+", s))
    for key in list(toks) + [app.name.lower()]:
        toks.update(_ALIAS.get(key, ()))
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}

def detect_data_dirs(app: AppInfo) -> List[Path]:
    """扫描 AppData/LocalAppData/文档 下名称匹配的目录，返回候选数据目录"""
    roots = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        r = Path(os.environ.get(env, ""))
        if r.is_dir():
            roots.append(r)
    docs = Path(os.environ.get("USERPROFILE", "")) / "Documents"
    if docs.is_dir():
        roots.append(docs)

    toks = _tokens(app)
    install = str(app.install_location).lower().rstrip("\\")
    results: List[Path] = []
    for root in roots:
        try:
            for entry in root.iterdir():
                if not entry.is_dir() or entry.is_symlink():
                    continue
                key = str(entry).lower().rstrip("\\")
                if key == install:
                    continue
                name = entry.name.lower()
                if any(t in name or name in t for t in toks):
                    results.append(entry)
                    break
        except Exception:
            continue
    # 去重保序
    seen, out = set(), []
    for p in results:
        k = str(p).lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out
