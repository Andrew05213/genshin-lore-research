# 文档导航

首次使用从根目录 [README](../README.md) 开始。本目录区分操作指南、实现说明与验证记录，避免把源码中存在的功能误认为已经完成部署。

| 文档 | 内容 |
|---|---|
| [当前状态](status.md) | 已提供入口、实际验证范围与剩余限制 |
| [Linux / Windows / 人物对白整合](reviews/linux-windows-integration.md) | 两个分支的保留范围、冲突处理、实际补测及未执行的整库验证 |
| [数据重建指南](guides/building-components.md) | 环境、固定上游源码、组件构建、输出、对照与排错 |
| [完整有界重建](guides/building-dataset.md) | 全部输入、统一索引、容量、运行配置与 MCP 启动 |
| [向量构建](guides/building-semantic.md) | 默认完整构建、固定模型、独立 overlay、核验与显式基础模式 |
| [完整冷构建性能](cold-build-throughput.md) | 默认向量单命令对照、固定阶段重叠、token 调度、完整来源/向量核对与资源代价 |
| [worker 后代退出修复](reviews/worker-tree-shutdown.md) | 父阶段失败、验证器后代、Linux/Windows 生命周期与定向回归范围 |
| [Linux 构建性能](linux-build-performance.md) | 实际耗时、embedding 调度优化、等价验证及未采用的索引实验 |
| [冷构建与阶段复用](build-reuse.md) | 进程内独立输入缓存、v3 回执、同条件耗时及未执行范围 |
| [非向量完整构建性能](nonvector-build-performance.md) | 两次新目录完整来源构建、保留/放弃的优化、完整核验与实际耗时 |
| [Windows 源码使用](guides/windows.md) | PowerShell、平台边界和原生 CI |
| [工程维护范围](engineering-maintenance.md) | 当前重构边界、复杂度控制与验证层次 |
| [工程整改交付（历史）](engineering-finalization.md) | 当时六个待审 PR、200 项测试、原生 CI、A/B、review 与执行范围 |
| [后续审查修复](reviews/engineering-followup.md) | 两项 Windows 实际路径缺口、正式关系 oracle 完整性及新增 12 项测试 |
| [完整重建实验](history/full-rebuild.md) | 完整链路实际执行、失败修复及验证结果 |
| [完整重建机器记录](history/full-rebuild.json) | 数据规模、artifact seals、MCP 覆盖与原始日志摘要身份 |
| [架构说明](architecture.md) | 数据流程、代码目录、Research 工具与证据语义 |
| [对白阅读](guides/reading-dialogue.md) | 来源角色、局部对白图、前驱、续读与任务标题候选 |
| [对白阅读验证](history/dialogue-reading.md) | 合成测试、真实来源、MCP 和模型检查的分别状态 |
| [目录与许可整理验证](history/repository-layout.md) | 命令迁移、测试、真实小样本构建与打包检查 |
| [开发指南](development.md) | 开发安装、测试、修改范围和文档维护 |
| [工程质量记录](engineering-quality.md) | 构建可信度、输出隔离、MCP 兼容、CI、实际执行与未运行项 |
| [目录与命名规范](naming.md) | 常用入口、旧命令迁移与保留历史名称的原因 |
| [源码分发与数据留存](distribution.md) | 分发文件、生成数据与许可状态 |
| [构建验证记录](history/component-build-validation.json) | 固定快照和已执行构建的机器可读结果 |
| [历史重建实验](history/component-rebuild.md) | 早期基础组件构建、初轮失败及当时的整库缺项 |
| [Agent 开发规则](../AGENTS.md) | Coding Agent 接手时遵循的规则 |

操作说明以实际脚本参数为准；验证结果仅适用于记录中明确列出的组件和范围。[初始源码包清单](history/initial-source-files.json) 记录初始发行包字节，后续 Git 改动不沿用其包级 seal。

日常操作文档位于本目录及 `guides/`；`history/` 保留实际实验与失败记录，不作为当前命令或许可配置。历史文件移入该目录不改变其原始验收含义。
