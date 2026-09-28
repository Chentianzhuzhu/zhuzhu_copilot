# -*- coding: utf-8 -*-
"""定位关键代码行号：输出 文件:行号: 内容。"""
import io

ROOT = r"C:\Users\zhuzhu\Desktop\my first android app"

TARGETS = {
    r"update-server\src\main\java\com\zhuzhu\update\web\UpdateController.java":
        ["downloadUrl", "/downloads/", 'out.put("md5"', "compare(", "X-Real-IP", "recordDownload", "getHeader"],
    r"update-server\src\main\java\com\zhuzhu\update\web\AdminController.java":
        ["existsByVersion", "storage.save", "version.trim", "countByVersionId", "forceUpdate"],
    r"update-server\src\main\java\com\zhuzhu\update\service\StorageService.java":
        ["transferTo", "public Path resolve", "sanitize", "baseDir", "getOriginalFilename"],
    r"update-server\src\main\java\com\zhuzhu\update\security\AdminAuthService.java":
        ["equals(", "UUID.randomUUID", "TOKEN_TTL", "adminPassword"],
    r"update-server\src\main\java\com\zhuzhu\update\config\WebConfig.java":
        ["addResourceHandler", "addPathPatterns"],
    r"update-server\src\main\java\com\zhuzhu\update\service\UpdateService.java":
        ["findFirstByOrderByCreatedAtDesc", "compare", "parseInt"],
    r"update-server\src\main\resources\application.yml":
        ["address:", "password", "ddl-auto", "max-file-size", "useSSL"],
    r"update-server\deploy\nginx.conf":
        ["location", "proxy_set_header", "server_name"],
    r"src\zhuzhu_Copilot\update_check.py":
        ["APP_VERSION", "POLL_INTERVAL", "DEFAULT_SERVER", "Chrome/", "self.server +", "urlopen"],
    r"installer\zhuzhu_Copilot.iss":
        ["MyAppPwd", "pfx", "addstore", "sign_uninstaller", "MyAppVersion"],
    r"build_sign.ps1":
        ["PfxPassword", "Export-PfxCertificate"],
    r"scripts\sign_uninstaller.ps1":
        ["Password", "AuthenticodeSignature"],
    r"scripts_nginx_check.py":
        ["HOST=", "PASS=", "USER="],
}

for rel, keys in TARGETS.items():
    path = ROOT + "\\" + rel
    try:
        lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
    except Exception as e:
        print("!! %s read fail: %s" % (rel, e))
        continue
    print("==== %s" % rel)
    for i, ln in enumerate(lines, 1):
        for k in keys:
            if k in ln:
                print("%s:%d: %s" % (rel.replace("\\", "/"), i, ln.strip()[:190]))
                break
