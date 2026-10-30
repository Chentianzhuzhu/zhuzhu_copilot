---
name: vibe-videos
description: 基于Remotion的炫酷视频生成器，支持音乐卡点特效和动画视觉元素
---

# vibe-videos：炫酷视频生成技能

## 触发条件
用户请求生成视频、描述想要制作炫酷视频、或明确要求使用music卡点特效时触发。

## 执行流程

### 阶段一：需求确认
1. 确认用户意图：
   - 视频主题/风格（赛博朋克、极简、复古、霓虹等）
   - 目标时长和帧率（默认30fps，60fps可选）
   - 分辨率（1080p默认，4K可选）
   - 是否有背景音乐文件（支持mp3/wav/flac）
   
2. 如果用户未提供音乐：
   - 询问是否使用内置示例音乐
   - 或生成纯视觉视频（无音频卡点）

### 阶段二：项目初始化
1. 创建Remotion项目（使用template-advanced或自定义）
2. 安装必要依赖：
   - @remotion/cli, @remotion/player
   - @remotion/audio-sprite（音频处理）
   - @remotion/ffmpeg（音视频处理）
   - three.js（3D特效）
   - react-spring（物理动画）
   - framer-motion（流畅动画）
   
3. 创建项目结构：
   ```
   video-project/
   ├── src/
   │   ├── components/
   │   │   ├── VisualElements/    # 视觉元素库
   │   │   ├── Effects/           # 特效组件
   │   │   ├── Animations/        # 动画序列
   │   │   └── Overlay/           # 叠加层
   │   ├── hooks/
   │   │   └── useAudioAnalysis.ts # 音频分析
   │   ├── scenes/                 # 场景配置
   │   └── index.tsx
   └── package.json
   ```

### 阶段三：视觉元素生成（核心）
**必须包含以下炫酷元素**：

#### 1. 粒子系统
- 动态粒子背景（数百至上千粒子）
- 粒子随音频波形/节拍变化
- 粒子颜色、大小、速度关联音频特征

#### 2. 几何图形动画
- 旋转的多面体（立方体、二十面体等）
- 脉冲缩放的环形/波浪
- 3D透视变换的卡片/平面

#### 3. 光效与发光
- 霓虹发光效果（使用CSS filter或SVG glow）
- 光线追踪模拟（射线/光束）
- 镜头光晕（lens flare）

#### 4. 文字特效
- 打字机效果（逐字显示）
- 故障艺术（glitch effect）
- 3D翻转/滚动文字

#### 5. 过渡动画
- 缩放/旋转/淡入淡出转场
- 波形展开/折叠
- 碎片重组效果

#### 6. 背景动态
- 渐变流动（animated gradient）
- 星空/粒子轨迹背景
- 网格线透视移动

### 阶段四：音频分析（关键）
1. 实现音频分析Hook：
```typescript
// useAudioAnalysis.ts
const useAudioAnalysis = (audioUrl: string) => {
  // 使用Web Audio API分析音频
  // 提取：波形数据、频率数据、节拍点
  // 返回：beatEvents, waveform, frequencyData
}
```

2. 节拍检测算法：
   - 使用onset detection算法
   - 标记每个重拍时间点
   - 计算BPM和beat间隔

3. 音频特征映射：
   - 低频（bass）→ 视觉冲击/缩放
   - 中频（mid）→ 颜色变化
   - 高频（treble）→ 粒子爆发/闪烁
   - 波形振幅 → 整体亮度/透明度

### 阶段五：卡点驱动动画
1. 设计动画时序表：
   - 每个beat对应特定的视觉触发
   - beat前准备动画（anticipation）
   - beat瞬间爆发（impact）
   - beat后衰减（settle）

2. 实现动画队列：
```typescript
const beatEffects = [
  { beat: 0, type: 'pulse', intensity: 1.0 },
  { beat: 1, type: 'explode', particles: 50 },
  { beat: 2, type: 'colorShift', hue: 180 },
  // ... 更多效果
];
```

3. 使用Remotion的useCurrentFrame和useAudioContext：
   - 根据当前帧位置计算音频播放进度
   - 实时响应音频变化
   - 同步视觉与音频

### 阶段六：场景编排
1. 定义场景结构：
   - 开场：标题动画（2-4秒）
   - 主体：随音乐推进的视觉序列（主体部分）
   - 高潮：特效密度最高区域
   - 结尾：渐出动画

2. 时间轴规划：
   - 使用Remotion的Sequence组件
   - 每个场景独立控制
   - 支持交叉淡入淡出

3. 随机性控制：
   - 可配置效果密度
   - 避免重复模式
   - 保证视觉节奏变化

### 阶段七：渲染与输出
1. 渲染配置：
   - 使用FFmpeg硬编码（H.264/H.265）
   - 设置合适的码率（目标文件大小）
   - 启用GPU加速（如有）

2. 输出格式：
   - MP4（H.264）默认
   - 可选WebM（VP9）用于web
   - 可选GIF（短视频片段）

3. 质量检查：
   - 验证音频同步
   - 检查帧率稳定性
   - 确认特效渲染完整

### 阶段八：交付与预览
1. 预览方式：
   - 使用preview_open打开视频文件
   - 提供Remotion Studio链接（可选）

2. 文件信息：
   - 输出文件大小
   - 分辨率和帧率
   - 总时长

## 关键规则

### 强制要求
1. **必须添加至少20种不同的视觉特效**：
   - 粒子系统、几何动画、光效、文字特效、过渡效果等
   - 每种特效需有独立的动画逻辑
   
2. **必须实现音频分析**：
   - 支持mp3/wav/flac格式
   - 至少检测：BPM、beat时间点、频谱数据
   
3. **必须实现卡点联动**：
   - 视觉元素随beat变化
   - 支持不同频率区间驱动不同效果
   
4. **性能优化**：
   - 粒子数量控制在合理范围（100-1000）
   - 使用requestAnimationFrame
   - 避免内存泄漏

### 可选增强
1. 3D场景（Three.js集成）
2. 物理模拟（弹簧/重力）
3. 用户自定义配色方案
4. 多种模板选择（赛博朋克/极简/霓虹等）

### 失败处理
1. 渲染失败时：检查日志，调整参数重试
2. 音频同步问题：重新计算时间轴
3. 性能瓶颈：降低特效复杂度或减少粒子数

## 示例调用
```
/vibe-videos
主题：赛博朋克风格
音乐：uploaded_audio.mp3
时长：30秒
分辨率：1080p
特效密度：高
```

或直接描述：
```
帮我生成一个炫酷的视频，背景音乐是xxx.mp3，要有很多粒子特效和光效，跟着音乐的节拍跳动。
```
