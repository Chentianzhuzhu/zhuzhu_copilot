---
name: web-search
description: 联网搜索：web_search 关键词检索（Bing/回退 DuckDuckGo），web_fetch 抓取网页/调用 API 并按关键词定位详情
---

# web-search：联网搜索与网页抓取

当需要查询实时信息、查找资料、调用网络接口时使用本技能。

## 1. 搜索
web_search(query=搜索关键词, max_results=返回条数, must_include=过滤词可选)：
- 适合：新闻、文档、教程、代码示例、产品信息等实时内容
- 返回标题+URL+摘要，先读摘要判断相关性，再决定是否抓详情
- 需要精确检索时传 must_include：只保留标题/摘要包含该词的结果

## 2. 抓取详情
web_fetch(url=地址, keyword=页面内关键词可选)，默认 GET；
- 抓取后定位：keyword 传目标词，只返回页面内包含该词的段落（长页/文档精确提取）
- 调用 API：method=POST/PUT/DELETE，headers 传鉴权头（如 {"Authorization": "Bearer xxx"}），
  body 传请求体（JSON 字符串或原始文本）
- 响应过长会截断，可指定 max_chars 调整

## 3. 信息不足时
换关键词重搜或用 ask_user 向用户确认方向，禁止编造内容与链接。

## 4. 汇报
给出结论并附来源 URL。