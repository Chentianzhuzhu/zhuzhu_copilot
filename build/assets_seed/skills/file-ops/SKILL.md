---
name: file-ops
description: 文件操作：read_file/write_file/edit_file/delete_file/list_directory 读写改删列文件，search_files 模糊查找
---

# file-ops：文件读写改删与查找

当需要读取、创建、修改、删除文件或查找文件时使用本技能。

## 1. 定位
- 知道路径：直接用 read_file / write_file / edit_file / delete_file / list_directory
  （相对路径基于工作目录，未设工作目录则基于用户目录）
- 不知道路径：search_files(query=文件名关键字, folder=限定目录可选) 模糊查找

## 2. 操作要点
- 读：read_file(path)，大文件分段读取
- 写：write_file(path, content)，会覆盖已存在文件，写前先确认目标
- 改：edit_file(path, old_text, new_text)，只替换首次匹配，old_text 需在文件中唯一
- 删：delete_file(path)
- 列目录：list_directory(path)

## 3. 注意事项
- 修改前先 read_file 了解原文，避免改错
- 系统关键目录（Windows、Program Files 等）的删除会被沙盒拒绝
- 操作后建议 read_file 验证结果
