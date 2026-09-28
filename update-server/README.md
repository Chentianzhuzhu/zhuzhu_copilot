# WinAppMigrator 更新服务器

官网 + Web 管理后台 + 客户端更新 API。架构：Spring Boot 3 + MySQL + Redis + nginx（HTTPS/域名）。

```
update-server/
├── pom.xml
├── deploy/nginx.conf          # nginx 配置（SSL + 域名反代）
├── src/main/java/com/zhuzhu/update/
│   ├── UpdateServerApplication.java
│   ├── config/WebConfig.java           # 拦截器 / 上传资源 / /admin 路由
│   ├── security/AdminAuthService.java  # 管理员登录（ADMIN_PASSWORD 环境变量 + Redis token）
│   ├── security/AdminAuthInterceptor.java
│   ├── entity/  repo/  service/        # 版本 / 下载日志 / 官网内容
│   └── web/
│       ├── SitePageController.java     # 官网服务端渲染 + robots.txt / sitemap.xml / 清单
│       ├── SeoSupport.java             # 站点地址推导、sitemap 生成、XML/JSON-LD 转义
│       ├── Icons.java                  # 特性图标白名单（线性矢量）
│       └── SiteController | UpdateController | AdminController
├── src/main/resources/
│   ├── application.yml                 # 全部敏感项走环境变量
│   ├── db/schema.sql                   # MySQL 建表（可选，JPA 也会自动建表）
│   ├── templates/                      # 官网服务端渲染模板（Thymeleaf）
│   │   ├── index.html  gallery.html  download.html  faq.html  error.html
│   │   └── fragments/  head.html  nav.html  blocks.html
│   └── static/                         # 静态资源 + 管理后台
│       ├── css/site.css  js/site.js
│       ├── favicon.ico  favicon.svg  apple-touch-icon.png  icon-*.png  og/og-cover.png
│       └── admin/      index.html  admin.css  admin.js
├── tests/                              # 官网静态回归校验（标准库 unittest，零依赖）
├── tools/                              # 本地开发辅助脚本（离线编译 / 单测 / 预览 / 品牌资源）
└── uploads/                            # 运行时生成：安装包 pkg/ + 官网图片 img/ + 视频 video/
```

## 0. 官网能力一览

- **服务端渲染**：页面正文由库内真实内容渲染，不执行 JS 的搜索引擎也能抓到完整内容与结构化数据。
- **完全后台可配置**：站名、标语、描述、主视觉图、分区文案、功能特性、界面实拍（图库）、
  视频演示、产品优势、常见问题、数据统计、系统要求、页脚与 SEO 字段均在 `/admin` 维护。
- **图片 / 视频展示位**：首页展示前 6 张图与前 4 个视频，`/gallery` 页展示全部；
  图片支持灯箱放大（键盘左右切换、移动端滑动），视频支持本地上传与外链嵌入两种来源。
- **SEO 全套**：`robots.txt`、`sitemap.xml`、web app 清单均由服务端按真实内容生成；
  另有 canonical、Open Graph、Twitter Card、JSON-LD（SoftwareApplication + 面包屑）与站点图标。
- **可访问性与降级**：无 JS 时内容照常展示、问答用原生 `details`；支持 `prefers-reduced-motion`。

## 1. 环境准备（服务器）

- JDK 21、Maven
- MySQL 8：建库 `winapp_update`（执行 `src/main/resources/db/schema.sql`，或让 JPA 自动建表）
- Redis 6+：默认连 `127.0.0.1:6379`

## 2. 配置环境变量（务必设置）

```bash
export ADMIN_PASSWORD='换成强密码'     # 管理后台登录密码（必须，空则后台拒绝登录）
export DB_HOST=127.0.0.1
export DB_PORT=3306
export DB_NAME=winapp_update
export DB_USER=root
export DB_PASSWORD='数据库密码'
export REDIS_HOST=127.0.0.1
export REDIS_PORT=6379
# export SITE_BASE_URL=https://chentian.dpdns.org  # 官网绝对地址（canonical / sitemap / robots），留空按请求头推导
# export UPLOAD_DIR=/opt/update-server/uploads   # 可选，默认 ./uploads
```

> 站点地图与分享卡片都依赖站点根地址。若前置了 CDN 或域名有变更，**建议显式设置 `SITE_BASE_URL`**，
> 否则回落到请求头（`X-Forwarded-Proto` / `X-Forwarded-Host`）推导，Host 头异常时会回落 `localhost`。

## 3. 构建与运行

```bash
mvn -DskipTests package            # 构建（CI 上会执行 mvn test 跑单元测试）
java -jar target/update-server-1.0.0.jar
# 服务只监听 127.0.0.1:8080
```

## 4. nginx + SSL + 域名

将 `deploy/nginx.conf` 放到 `/etc/nginx/conf.d/`，替换 `example.com` 与证书路径：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

外网访问：`https://example.com`（官网）与 `https://example.com/api/update/check?version=1.0.0`（客户端轮询接口）。
**管理后台 `/admin` 未在 nginx 放行**，外网不可达，只能走 SSH 隧道。

## 5. 通过 SSH 隧道访问管理后台

```bash
# 本机执行：把服务器的 8080 端口映射到本地 8080
ssh -N -L 8080:127.0.0.1:8080 user@服务器IP
# 然后浏览器打开 http://127.0.0.1:8080/admin 输入 ADMIN_PASSWORD 登录
```

后台功能：内容管理（文案 / 图库 / 视频 / FAQ / 数据 / 页脚 / SEO）、媒体库（图片与视频上传、删除、
复制地址）、版本发布（安装包上传）、统计图表（总下载量与 30 天趋势）。

## 6. 客户端接入

客户端轮询接口（每 30s）：

```
GET /api/update/check?version=1.0.0&platform=windows
→ {"hasUpdate":true,"latest":"1.1.0","notes":"...","size":52428800,
   "md5":"...","force":false,"url":"/api/update/download/12"}
```

下载：`GET /api/update/download/{id}`（返回安装包文件流并记录下载量）。

## 7. 搜索引擎收录检查清单

| 项目 | 地址 | 说明 |
| --- | --- | --- |
| 站点地图 | `/sitemap.xml` | 由控制器按真实页面生成，覆盖首页 / 界面实拍 / 下载 / 常见问题 / 关于 |
| 抓取规则 | `/robots.txt` | 放行全站，屏蔽 `/admin` 与 `/api/`，并声明 Sitemap |
| 结构化数据 | 页面内嵌 JSON-LD | SoftwareApplication + WebSite + BreadcrumbList，`/faq` 另含 FAQPage |
| 站点图标 | `/favicon.ico` `/favicon.svg` `/apple-touch-icon.png` | 多尺寸，含 SVG |
| 应用清单 | `/site.webmanifest` | 按库内站名生成 |
| 分享主图 | `/og/og-cover.png` | 1200×630，可在后台「SEO → 社交分享图」替换 |
| 静态官网 | `/website/` | 独立单页，由 nginx 直接提供（文件放 `/var/www/website/`），SEO 产物见仓库 `website/` |

> **静态官网的部署**：`website/` 是纯静态目录，改完 SEO 后执行 `python scripts/gen_site_seo.py`
> 重新生成，再把 `index.html` / `robots.txt` / `sitemap.xml` / `design_preview.png` 复制到服务器的
> `/var/www/website/`（nginx 规则见 `deploy/nginx.conf` 的 `location /website/`）。
> 它的 `siteUrl` 在 `website/site.seo.json` 里，与本站同域不同路径。

> **双品牌收录**：后台「页脚信息与 SEO」提供 `seo.keywords`（搜索关键词）与 `seo.alternateNames`（副品牌别名）。
> 后者写入结构化数据的 `alternateName`，让「zhuzhu Copilot」与「WinAppMigrator」被识别为同一款软件，
> 而不是两个互不相干的站点。改站名时请一并更新这两项。

> **上线后若出现「页面几乎空白 / 只剩一张图标 / 样式与脚本全失效」**，先别改代码 ——
> 这通常是 CDN 的全站质询，而不是源站问题。用下面两条命令即可在 10 秒内区分：
>
> ```bash
> # 1) 源站：应当全部 200，且 CSS/JS 的 MIME 正确
> ssh 服务器 'for p in / /css/site.css /js/site.js; do printf "%-16s " $p; \
>   curl -s -o /dev/null -w "code=%{http_code} type=%{content_type}\n" http://127.0.0.1:8080$p; done'
> # 2) 域名：若返回 403 且带 Cf-Mitigated: challenge，就是 CDN 质询
> curl -sI https://你的域名/ | grep -iE 'HTTP/|cf-mitigated|server'
> ```
>
> 常见触发项（Cloudflare 控制台 → 选择该域名 →「安全性 / Security」）：
>
> | 触发项 | 位置 | 处置 |
> | --- | --- | --- |
> | 安全级别 =「我正在遭受攻击!」 | 安全性 → 设置 → 安全级别 | 改回「中 / Medium」 |
> | Bot Fight Mode | 安全性 → 机器人 | 关闭，或加 WAF 跳过规则 |
> | 浏览器完整性检查 | 安全性 → 设置 | 关闭 |
> | 全站 Managed Challenge 规则 | 安全性 → WAF → 自定义规则 | 放行静态资源与搜索引擎 UA |
>
> 质询会同时拦掉 `/css/*`、`/js/*` 等子资源（拿到的是 HTML 而非 CSS/JS），
> 但 Cloudflare 对 `/robots.txt` 有特殊放行 —— 所以「robots 正常、其他全 403」是该问题的典型指纹。
> 处理后在无痕窗口打开（或强刷）验证，避免旧 `cf_clearance` Cookie 干扰。
>
> 另需注意：本服务器的证书由 Cloudflare Origin CA 签发，**浏览器不信任**。
> 因此不能通过「把 DNS 改为仅 DNS（灰云）」来绕过质询，除非先换成受信任证书（如 Let's Encrypt）。

## 8. 本地开发与校验（无需 Maven 也能跑）

```bash
# 一次性补齐本地缺失的编译期依赖（spring-data-jpa / jakarta.* / thymeleaf / JUnit 控制台）
python tools/fetch_offline_libs.py

python tools/offline_compile.py                  # 快速编译校验
python tools/run_tests.py                        # 跑单元测试（含真实模板渲染）

# 生成品牌资源（站点图标 + 分享图，站名与线上保持一致）
python tools/gen_brand_assets.py --name "zhuzhu Copilot"

python -m unittest discover -s tests -v          # 官网静态回归校验

# 本地真实预览（用线上内容渲染 + 回放真实接口）
python tools/run_tests.py --preview --preview-content target/preview/live-site.json
python tools/preview_server.py --port 8099        # 打开 http://127.0.0.1:8099
python tools/inspect_preview.py index.html        # 打印渲染结果的关键结构与 SEO 元素
```
