# UI Demo 项目

综合UI演示项目，包含6个视图：

1. **数据仪表盘** - 指标卡 + Canvas图表（折线图/饼图）
2. **后台管理** - 用户表格 + 分页 + 添加表单
3. **产品落地页** - Hero区 + 特性卡片 + 定价方案
4. **任务看板** - HTML5拖拽看板
5. **作品集** - 滚动入场动效（IntersectionObserver）
6. **组件库** - 按钮/卡片/模态框/Toast/标签页

## 技术栈

- 原生 HTML5 + CSS3 + JavaScript
- Canvas API（图表）
- IntersectionObserver API（滚动动效）
- HTML5 Drag & Drop API（看板拖拽）

## 运行方式

双击 `index.html` 直接打开，或使用本地服务器：

```bash
python -m http.server 8080
```

然后访问 http://localhost:8080

## 设计特点

- 纯黑+淡灰+白+深蓝配色方案
- 简约无emoji的矢量图标风格
- 深色/浅色主题切换
- 响应式布局

## 文件结构

```
ui_demo/
├── index.html      # 主页面
├── css/
│   └── style.css   # 样式表
├── js/
│   └── app.js      # 应用逻辑
└── images/         # 图片资源目录
```
