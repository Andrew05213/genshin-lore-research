# 最终提交审查：三项缺口修复

审查基于 `e799674d7537b23b9ac00798283b009142c8e3ef`。原 CI 的 200 项通过没有覆盖下述实际路径，不能据此否定 findings；原成功/失败记录保留。

| Finding | 最小修复 |
|---|---|
| Windows 构建器对只读句柄/目录刷盘 | M3 调用既有 `fsync_file`；NPC/Dialog/Reminder 调用相同文件 helper 与 `sync_directory`。Windows 文件以可写 descriptor 刷盘，目录保留明确的平台持久性边界；不吞掉文件刷盘错误。 |
| 容量门禁提前绝对化 `/tmp` | 临时发现入口、约定路径识别和 `ProjectSpaceGate` 统一在绝对化前选择系统临时目录；显式 custom root 不改写。叙事构建的模型目录统计使用该已解析根。 |
| 正式 adapter 关系被整体遗漏仍通过 | 独立 oracle 从静态关系/FK 合同和实际父项范围计算来源预期数量；candidate 不依赖索引中幸存的 relations 来确定分母；校验实际数量和声明状态。 |

[Windows API 要求](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers)刷盘 handle 具有写权限。未更改 SQL、来源字段、候选身份或历史发布合同。

## 实际验证

- 新增四项平台测试，执行实际的小型 M3/NPC 构建与容量门禁，不只 import helper；包括 writable flush、目录 helper、默认 string/Path 临时根、显式根保留和不覆盖产物。
- 新增七项独立 oracle 测试，包含完整/空表、全关系遗漏、候选遗漏、状态降级、零/部分父项范围。修复前实际复现两个错误放行；候选单独遗漏原先已被逐行 cardinality 拒绝，不误称原先也会通过。见 [关系补丁记录](pr-1-relation-closure.md)。
- 合并全部补丁后的本地 **211 项通过**（Python 3.12）；原生 Windows/Ubuntu 最终状态见 PR checks。[首个 Windows 修复提交的两平台 CI](https://github.com/Andrew05213/genshin-lore-research/actions/runs/39095040182) 已通过，后续父分支同步另行触发检查。
- 独立遗漏负测在 `python -O` 下同样明确拒绝，不依赖 Python assert。
- 从已有干净固定 7.1 source 重新执行真实 Activity 和 NPC 定向构建及独立来源 oracle，均 PASS；在 Linux 执行，不冒充 Windows 全量来源验收。Activity 为 94,208 B，SHA `40c7275eaa018959ce3da97e3736a675021814cb5f85dc0765100c3c2d73598e`；NPC 为 24,797,184 B，SHA `538cb73d037fbf2a23d8b6d1ce019bb2320106406d50f125a92e604b104620e8`。
- 对保留正式 index 的 Document 执行新来源范围检查：9,937 relations、9,905 candidates 与实际来源一致；只读连接，index stat 不变。这不是整库重新资格验证。

初次测试的错误 checkout 导入和过宽的模拟 OS monkeypatch 已修正，原输出留在本地执行记录；未靠放宽 checker 通过。原 partial fixture 补齐真实 source rows/FK，保持 partial 正向断言。

## 交付边界

Windows 修复在 PR #4，oracle 修复在 PR #2；沿原堆叠关系同步到 #3/#5/#6，PR 与 main 均不自动合并。源码许可保持 Apache-2.0；不提交游戏正文、数据库、模型、向量或本机配置。

构建器源码改变会产生新的实现/parser/run fingerprint，oracle 修改也使旧成功回执不能自动恢复。受影响来源定向验证已本次执行；**完整 35 输入及全游戏向量重建仍未执行/未验收**。不沿用历史成功，不修改正式部署、旧发布 receipts 或 evidence ref。完整重建命令与新增峰值说明仍见 [构建指南](../guides/building-dataset.md)。
