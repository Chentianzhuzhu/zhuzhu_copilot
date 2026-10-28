---
name: audio-analyzer
description: 音频分析插件 - 提供音频上传、分析、频谱可视化、音高检测、节拍识别、音轨分离等能力
---

# 音频分析插件

本插件提供完整的音频分析能力，支持 MP3/WAV/FLAC 等常见音频格式。

## 可用工具

| 工具名 | 功能 |
|--------|------|
| `upload_audio` | 上传音频文件 |
| `analyze_audio` | 获取音频基本信息（时长、采样率、声道数） |
| `get_spectrum` | 获取频谱数据用于可视化 |
| `predict_pitch` | 检测音高（Hz） |
| `detect_beat` | 检测节拍时间点 |
| `extract_tracks` | 分离音轨（人声/鼓/贝斯/其他） |
| `get_status` | 查询任务状态 |

## 典型使用场景

1. **音频基础分析**：上传后调用 `analyze_audio` 获取元数据
2. **频谱可视化**：获取频谱数据后在前端 Canvas 绘制
3. **音高检测**：对特定时间段调用 `predict_pitch`
4. **节拍分析**：调用 `detect_beat` 获取节拍时间点

## 参数说明

- `file_path`: 音频文件本地路径（绝对路径）
- `file_id`: 上传后返回的标识符
- `start_time`: 起始时间（秒）
- `duration`: 时长（秒）
- `window_size`: FFT 窗口大小（默认 2048）
- `hop_length`: 步长（默认 512）

## 返回值格式

所有工具返回 JSON 格式结果，包含 `success` 字段表示是否成功。
