# 文档导航

首次使用从根目录 [README](../README.md) 开始。本目录区分操作指南、实现说明与验证记录，避免把源码中存在的功能误认为已经完成部署。

| 文档 | 内容 |
|---|---|
| [当前状态](STATUS.md) | 已提供入口、实际验证范围与剩余限制 |
| [数据重建指南](REBUILDING.md) | 环境、固定上游源码、组件构建、输出、对照与排错 |
| [完整有界重建](COMPLETE_REBUILD.md) | 全部输入、统一索引、容量、运行配置与 MCP 启动 |
| [完整重建实验](COMPLETE_REBUILD_EXPERIMENT.md) | 完整链路实际执行、失败修复及验证结果 |
| [完整重建机器记录](COMPLETE_REBUILD_EXPERIMENT.json) | 数据规模、artifact seals、MCP 覆盖与原始日志摘要身份 |
| [架构说明](ARCHITECTURE.md) | 数据流程、代码目录、Research 工具与证据语义 |
| [开发指南](DEVELOPMENT.md) | 开发安装、测试、修改范围和文档维护 |
| [源码分发与数据留存](PUBLICATION.md) | 分发文件、生成数据与许可状态 |
| [构建验证记录](BUILD_VALIDATION.json) | 固定快照和已执行构建的机器可读结果 |
| [历史重建实验](REBUILD_EXPERIMENT.md) | 早期基础组件构建、初轮失败及当时的整库缺项 |
| [Agent 开发规则](../AGENTS.md) | Coding Agent 接手时遵循的规则 |

操作说明以实际脚本参数为准；验证结果仅适用于记录中明确列出的组件和范围。源码包文件的 size/SHA 列在 [分发清单](../PUBLIC_SOURCE_FILES.json) 中。
