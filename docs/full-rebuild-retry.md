# 完整来源重建：失败、最小修复与复验

本记录补充 [工程质量记录](engineering-quality.md)，不替代独立来源验证或正式发布资格。基线为 PR #2 的 `27b4ed3940616e3781108a53a1d1544cc2a36014`；未合并 PR，未修改 main 或正式部署。

## 实际执行

两次均使用已有的干净固定来源 checkout，从空输出目录执行 `scripts/build.py --stage all`，未复制旧数据库或复用旧回执。不是重新联网获取全部来源的实验。

| 尝试 | 实现基线 | 实际结果 |
|---|---|---|
| 01 | PR #2 | **FAIL**，28:15.72。42 个阶段通过；R0 原生报告有正确文件 seal，但调度器读取了另一种字段布局，独立 R0 oracle 尚未执行。 |
| 02 | `8e7a0df6a6d9646055a3bf9c5dd0c27363b56d06` | **FAIL**，29:57.82。49 个阶段通过，包含 R0、Readable、Subtitle；Event Filmfest 在任务创建前读取不存在的源码树动态 manifest，触发 FileNotFoundError。 |

阶段数不等于 35 个最终输入数量，也不代表游戏内容覆盖率。两次的完整统一索引、最终 runtime 配置与端到端检查均 **NOT_RUN**，不得据合成测试或局部成功宣称完整重建通过。

## 最小修复

- R0 调度边界保留原生 `artifact_size_bytes/artifact_sha256`，补充通用 `artifact` 字段；仍核对实际字节、拒绝 sidecar，且必须通过原外部独立 oracle。尝试 02 实际核验 162,309 个文件、14,401,737 个来源 occurrence，坐标一致且 artifact 未变。
- Filmfest 显式传递本次输出目录的 Quest/Talk manifest，贯穿候选发现、构建和独立验证。构建 scope 时先从来源推导候选；冻结后的 scope 在 projection 和独立 oracle 中仍要求完整候选集合相等。不会退回其他输出目录。
- 保留历史调用的默认参数形式；完整构建显式使用自己的动态绑定。独立验证器继续自行重建来源行集，不调用 builder 投影。

Filmfest 使用实现 fingerprint；本次模块修复会生成新的 parser/run 身份。未修改历史 parser/run、来源范围、证据状态、SQL 或封存 artifact。调度器的保守实现身份亦改变，不能将前次阶段回执直接用于修复后的完整重建。

## 定向复验与资源

修复后的 Filmfest 单组件真实来源重建及独立 oracle **PASS**：133 个物理记录、768 个文本槽、702 个文本 occurrence、133 条关系、133 条候选；5 个外部 Talk 目标逐项回读。此项显式复用尝试 02 的已验证 Quest/Talk 输入，不冒称第三次全量构建。

合成回归覆盖 R0 报告适配、错误大小/SHA、独立验证未执行拒绝，以及 Filmfest 动态路径、双输出隔离、来源回读和冻结候选漂移拒绝。本次全部 152 项合成测试通过，Python 编译通过；远端 CI 未运行。原始日志、失败回执、资源采样与输入 seal 本地保留，不提交游戏正文或数据库。

平台为 Python 3.12.3 / SQLite 3.45.1，约 16.76 GB RAM、4.29 GB swap。尝试 02 进程最大 RSS 为 2,026,224 KiB，未观测到新增 cgroup OOM；尚未执行统一索引阶段，不能据此宣称全链内存峰值已验证。

再次完整执行必须先实测项目与文件系统余量，并使用新的输出目录。命令与资源门槛见 [构建指南](guides/building-dataset.md)；原失败目录不得静默重置或伪装成成功。
