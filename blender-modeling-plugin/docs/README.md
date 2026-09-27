# Blender Advanced Modeling Plugin

基于Blender Python API的建模插件，提供专业建模工具集。

## 功能概述

### 基础建模工具
- 创建基本几何体（立方体、圆柱、球体、圆锥、圆环等）
- 布尔运算（切割、合并、交集）
- 网格编辑（挤出、填充、刀具切割）
- 细分表面、倒角、实体化等修改器

### 选择工具
- 按类型选择（顶点、边、面）
- 随机选择
- 链接选择
- 按材质选择

### 变换工具
- 变换复制/粘贴
- 应用变换
- 重置变换
- 对齐视图
- 吸附到光标/网格

## 安装说明

### 方法一：手动安装

1. 下载插件包
2. 打开Blender
3. 进入 Edit > Preferences > Add-ons
4. 点击 Install
5. 选择 `__init__.py` 文件
6. 勾选启用

### 方法二：复制到插件目录

将插件文件夹复制到Blender的用户脚本目录：

```
Windows: %APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\
Mac: ~/Library/Application Support/Blender/<version>/scripts/addons/
Linux: ~/.config/blender/<version>/scripts/addons/
```

## 使用方法

### 侧边栏访问

安装后，在3D视图右侧会出现 "Modeling" 标签页，包含四个面板：

1. **Advanced Modeling Tools** - 基础建模工具
2. **Geometric Tools** - 几何修改工具
3. **Selection Tools** - 选择工具
4. **Transform Tools** - 变换工具

### 菜单访问

所有工具也可通过菜单访问：
- Mesh > Advanced > (工具名称)

## 代码结构

```
blender-modeling-plugin/
├── __init__.py           # 插件入口，注册类
├── operators/
│   ├── __init__.py
│   ├── primitive.py      # 基础体创建
│   ├── boolean.py        # 布尔运算
│   ├── modifiers.py      # 修改器操作
│   ├── mesh_operations.py # 网格编辑
│   ├── selection.py      # 选择工具
│   ├── transform.py      # 变换工具
│   └── ui_panels.py      # UI面板
├── models/
│   ├── __init__.py
│   ├── mesh_utils.py     # 网格工具函数
│   └── transform_utils.py # 变换工具函数
├── utils/
│   └── __init__.py
├── tests/
│   ├── test_modeling.py  # 单元测试
│   ├── smoke_test.py     # 冒烟测试
│   ├── conftest.py       # pytest配置
│   └── requirements_test.txt
└── docs/
```

## 依赖

- Blender 3.0+
- Python 3.8+
- bpy (Blender内置)

## 开发指南

### 添加新功能

1. 在 `operators/` 下创建新模块
2. 在 `__init__.py` 中注册新类
3. 在 `ui_panels.py` 中添加UI元素

### 运行测试

```bash
python tests/smoke_test.py
```

## 许可证

MIT License

## 作者

zhuzhu Copilot

## 版本历史

- v1.0.0 - 初始版本，包含基础建模工具
