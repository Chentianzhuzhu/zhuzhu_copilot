---
name: image-gen
description: 调用 Agnes Image 2.1 Flash API 进行文生图和图生图，生成后自动下载到桌面
---

# image-gen：Agnes Image 2.1 Flash 图像生成

当用户要求"生成图片 / 画一张图 / 图生图 / AI绘图 / 图像生成"时使用本技能。

## 1. 确认需求（先询问，频率尽量少）

调用 ask_user 确认以下信息，禁止猜测：
- **生成方式**：文生图（纯文本描述）还是图生图（基于已有图片修改）？
- **prompt**：具体的文字描述（中文或英文均可）
- **size**：1K / 2K / 3K / 4K（推荐2K，默认）
- **ratio**：1:1 / 3:4 / 4:3 / 16:9 / 9:16 / 2:3 / 3:2 / 21:9（默认1:1）
- **图生图时**：输入图像的URL或Base64数据

> 说明：除非用户明确要求，否则默认使用 **文生图 + 2K + 1:1 + URL输出**，不要反复询问。

## 2. API Key（已内置，无需询问）
```
sk-IYR7ahd4hRxqfbsyEx9btXycbZPzxTaV829MGLchJeCtZf5x
```

## 3. 构建请求
- 文生图：{model, prompt, size, ratio, extra_body:{response_format:"url"}}
- 图生图：在文生图基础上加 image:["<URL或Base64>"]
- 多图合成：image:["<URL1>","<URL2>"]

## 4. 执行请求（重要：等待规则）
**必须使用 curl 命令执行 API 请求，禁止生成脚本文件。**

> ⚠️ **图片生成耗时较长（通常 30~90 秒甚至更久）。**
> 执行 curl 时 `run_command` 的 `wait` 参数**必须至少 60 秒**（建议 wait=90），**不要开启 `force_quit`**。若 60 秒内未完成会自动转入后台，此时必须用 `check_command` 持续轮询，直到命令结束并拿到 API 返回的图片 URL，严禁短等待后直接放弃或重复提交。
> 严禁使用默认 wait=5 秒或更短的等待执行图片生成请求，否则会误判超时、拿不到结果或重复计费。

curl 示例（文生图）：
```bash
curl https://api.agnes-ai.cn/v1/images/generations \
  -H "Authorization: Bearer sk-IYR7ahd4hRxqfbsyEx9btXycbZPzxTaV829MGLchJeCtZf5x" \
  -H "Content-Type: application/json" \
  -d '{"model":"agnes-image-2.1-flash","prompt":"<prompt>","size":"1K","ratio":"1:1","extra_body":{"response_format":"url"}}'
```

## 5. 下载保存图片（默认保存至桌面）
API 返回图片 URL 后，用 curl 下载到桌面，文件名为 `图片_{时间戳}.{格式}`。

> ⚠️ 下载大图（2K/3K/4K）同样需要时间，执行下载 curl 时 `wait` **至少 60 秒**，未完成用 `check_command` 轮询。

```bash
DESKTOP=$(powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" | tr -d '\r')
TS=$(python -c "import time;print(int(time.time()))")
curl -o "$DESKTOP/图片_${TS}.png" "<API返回的URL>" --connect-timeout 10 --max-time 60
```

> 规则：图片**默认始终保存到桌面**，除非用户明确要求保存到指定路径；严禁保存到工作目录等其他位置。
