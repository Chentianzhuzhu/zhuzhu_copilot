---
name: systematic-debugging
description: 系统化调试：复现问题、假设根因、逐一验证，不靠猜测
---

# systematic-debugging：系统化调试

1. 复现问题并读取相关日志/输出（run_command 或 read_file）。
2. 提出最可能的 2-3 个根因假设，按可能性排序。
3. 逐个用最小实验验证假设，排除一个再验证下一个。
4. 定位根因后修复，再复现验证已解决。
