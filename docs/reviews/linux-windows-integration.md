# Linux、Windows 与人物对白整合验证

## 输入与保留范围

2026-10-24，在独立分支 `codex/integrate-linux-windows-dialogue` 合并两个精确输入，不合并 main、不修改原分支：

- [PR #7](https://github.com/Andrew05213/genshin-lore-research/pull/7)：`e86991124edade09efc1c7bc4eac8a9104a05cab`，已包含 [PR #8](https://github.com/Andrew05213/genshin-lore-research/pull/8) 的 NPC→来源标注发言、剧情标题、前后文、cursor 及引用 handoff。
- [PR #9](https://github.com/Andrew05213/genshin-lore-research/pull/9)：`5c421f7f3f00a42e22838110e2ed61edf2cd08fe`，保留原生 Windows 文件/URI/HNSW 修复、本次构建依赖绑定、流式投影、完整 oracle、磁盘 staging 与失败现场保留。

共同基线为 `cf1788f3696cb07eae31c60b79539458936e6ce6`。保留 Apache-2.0、既有 evidence ref、候选限制、历史失败及原发布身份。未改变查询默认 literal，也没有新增 MCP 工具、数据范围、模型或关系类型。

## 冲突处理

文本冲突只在 `scripts/builders/semantic.py`。采用一条推理流程：当前调用内逐字相同窗口只计算一次；唯一文本按长度稳定组批；结果散写到全部原 vector ID；HNSW 仍按原物理 ID 单线程插入，并使用 Windows 可用的原生文件传输 helper。

保留 Linux 默认最多 4 个 affinity-visible 构建线程、其他平台默认 2 线程，以及显式 `--embedding-threads` 和恢复身份。查询线程仍为 2。清除自动合并留下的重复 helper 定义和重复 timing 字段；采用包含 selection、embedding（含模型初始化）、ANN、lexicon 的阶段时间记录。

Research 采用 #9 的统一磁盘 staging，而不是为 Linux/Windows 维护两套构建器。它解决内存峰值，但不宣称整库提速；#9 的局部实测包含建库时间上升。#7 的原性能记录保留为历史证据，不被用作组合实现的新性能结论。

## 本次实际执行

| 检查 | 结果 |
|---|---|
| 全部合成测试 | PASS：556 项，138.01 秒，无失败/跳过 |
| 组合回归 | PASS：Linux/Windows 默认线程、长度排序、重复推理复用、所有物理向量 ID、中文 HNSW 输出、独立映射 oracle 与运行时候选回读共同执行 |
| 真实 hnswlib | PASS：显式 `tests/semantic_native.py` 7 项；包括中文/空格/非 BMP 路径、损坏与保存失败，不下载模型 |
| 人物对白 | PASS：既有人物→Codex、来源姓名/标题/正文、相邻节点、cursor、exact read/inspect、候选状态和 citation handoff 测试保留并执行 |
| MCP 与全文 | PASS：既有真实合成子进程五工具、生成配置、分页、对白 context、Book/Story 续读与引用测试；不冒称新的真实模型会话 |
| 编译、六命令 help、链接与许可 | PASS；help/链接/许可也在 556 项中，不重复累计 |
| wheel / 依赖 | PASS：构建与 Apache-2.0 元数据，包含新阅读器/HNSW 模块，无数据库、模型或向量文件；同一锁定测试环境的 `uv pip check` 核对 42 个包兼容，仍是源码 checkout 发行策略 |
| Activity / NPC 来源 A/B | PASS：两组各自从干净固定来源 fresh build，独立 oracle 均通过；对应 artifact 字节完全相同 |
| 固定真实模型有界比较 | PASS：64 条真实来源、70 个窗口，原 #7、原 #9、整合 2 线程及整合 4 线程的向量逐字节一致；最大绝对差为 0 |

真实模型样本的 70 个窗口均不同，因此它不证明真实重复输入的全部情形；重复计算复用与不合并物理身份由上述合成组合测试另行覆盖。模型保持 multilingual MiniLM / 384 维 / 原固定 revision。本次不是吞吐量实验，不从单次耗时宣称加速，不代表全部 106,589 个窗口重新验收。

来源固定 commit 为 `9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99`、tree 为 `aa578c08c402af39b41f1ffc95124d10f5410272`。A/B 使用独立、最小的干净 sparse worktree，不覆盖旧来源目录的既有改动。

| fresh artifact | 每组 size / SHA-256 |
|---|---|
| Activity | 94,208 B / `e703916443a52adf04c400ef31671179876c31c562ac3f71b2f74b9bad6e6e13` |
| NPC | 24,797,184 B / `8af64555dc244618322954f68e2a1efe228d6fb185c8d874a6d0fe810efa8c13` |

模型比较输入身份摘要为 `61cd0a9a37e2bfe51918d9f755bc0be8bf41be168495a23338d3bd8e9de785ca`；四组 float32 向量摘要均为 `fe5b5092dee652c86e48354daa49f8b677e8d25f30a19caa9c3923db5076cd18`。原始来源、测试日志和新产物仅本地留存，不随 Git 分发。

## 保留的失败及未执行范围

- 初轮为 552 PASS / 1 FAIL：旧调度测试仍按单参数进度回调及重复计算次数断言。统一内部回调后，测试分别验证 129 个唯一窗口与 129 个含重复窗口的情况，仍逐行核对全部 129 个向量及输入顺序；未删除负测或放宽 oracle。
- 首次来源命令因旧来源 checkout 的 README 已删除而拒绝。没有恢复他人改动、忽略 dirty 或复用旧回执；改用本轮独立干净来源并保留失败记录。
- 本地锁定测试环境没有 pip；最初 `pip check` 未能执行。改用同一环境的 `uv pip check`，显式将 cache 放在本轮输出目录后通过；没有修改依赖、冒称原命令成功或写入用户只读 cache。
- FastEmbed 原有 pooling 提示与 ONNX telemetry 提示保留，未改模型或静默改 pooling。
- NOT_RUN：整合实现的完整 35 输入/全量向量重建、原生 Windows 完整来源再验收、真实 Agent 最终答案矩阵。父分支的完整成功是各自精确实现的证据，不自动迁移成整合版本的全量成功。
- 不更新部署或正式 release/current，不以本次补测作为 promotion。已有构建回执仍核对实现 fingerprint，不把父分支 receipt 改标签继续使用。

整合 PR 的 Ubuntu/Windows CI 与只读 review 结果见其 checks；它们不替代上列未执行的高成本验证。
