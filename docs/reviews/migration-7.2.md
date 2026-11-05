# 固定 7.2 来源的有界数据迁移

下文及 JSON 保留原 Linux 迁移实验的执行范围和当时结论。
后续 review 修复后的新 35 输入冷构建、默认向量和真实 Codex 会话在原生 Windows
另行完成，见 [Windows 复验](migration-7.2-windows.md)；不改写下文的原始失败或 seals。

本次完成现有 **35 个 Research 输入、33 个 Schema20 core providers** 的
7.2 来源构建、完整索引核对、新向量构建和程序化 MCP 验证。
这是源码构建及本地候选资格，不是全游戏覆盖、正式 7.2 发布或 Agent 答案验收。
既有 7.1 客户端、安装器默认、正式 release 和其他工作区保持不变。
机器可读的 seals、计数与原始记录摘要见 [本次验证记录](migration-7.2.json)。

## 固定来源与范围

- 上游：`DimbreathBot/AnimeGameData`。
- Commit：`d54f6f0d423ded9d055bb740a3e5f3aa8f2667f1`。
- Tree：`5b334e711319726c504da95dbfe6ebb26baaa7d3`。
- Snapshot：`anime-game-data:7.2:d54f6f0d423ded9d055bb740a3e5f3aa8f2667f1`。
- Research corpus：`candidate-7.2-high-value-v1`。

新目录中取得的 156,131 个文件、4,151,856,370 字节逐项与固定 Git blob 核对，
checkout 保持干净。初次 lazy Git checkout 的网络停滞及中断记录保留；随后使用
commit 固定归档取得文件并验证全部 blob，不把归档文件名当作来源证明。

新库从来源构建，不复制或 patch 旧数据库。沿用 Schema20，不需要 Schema21；
SQL 定义没有为游戏版本号改版。所有输入、parser/run、动态绑定、Research refs
和新向量指向固定 7.2，未沿用旧数据的 acceptance 或向量结果。

| 来源物理范围 | 固定 7.1 | 固定 7.2 |
| --- | ---: | ---: |
| Quest 文件 | 4,808 | 4,543 |
| Talk 文件 | 59,853 | 57,643 |
| Readable 文件 | 28,785 | 29,175 |
| Subtitle 文件 | 4,644 | 4,839 |

这只是所选快照的物理文件清单变化，不证明官方首次实装、删除或逻辑实体对应。
原有 8 项历史合同差异与 12 条组件记录没有因此关闭或改计分母。

## 实际装配与核验

全部 core、Character foundation/text/lore/voice、NPC/Dialog/Reminder、Quest/Talk、
Quest Narrative/Review/binding、Codex、Document、Readable、Subtitle、Mail、GCG、
LoadingTips、9 个 Event 输入和 Native 6 个 run 均完成来源构建及独立核对。
共有 60 个数据库/索引阶段的 v3 资格回执，另有 semantic 专用回执；
这些数量不是游戏数据覆盖率。

统一索引的 staging 和最终只读文件映像分别执行完整 oracle，均返回
`PASS_COMPLETE_SOURCE_ROWSETS`。核对包括全部正文、容器、关系、候选和 FTS，
没有以代表性抽样代替完整行集验证。

| 索引对象 | 实际行数 |
| --- | ---: |
| source artifacts | 35 |
| adapter coverage records | 195 |
| physical containers | 927,621 |
| text occurrences | 3,735,403 |
| relations | 660,120 |
| candidates | 434,558 |

索引为 **6,649,913,344 B**，SHA-256：
`48662d870bff428527b87ee63cae33b2306d829548f7d000106654fcdc340aa3`。
R0 是另外绑定的来源依赖；没有为了合并而把所有输入复制进一个大库。

向量从新索引重新生成：MiniLM、384 维、cosine HNSW，
103,188 个物理 occurrence / 104,558 个窗口。
相同输入字符串可以复用推理计算，但每个物理窗口保留自己的映射及 ref。
独立 oracle 检查全部窗口、来源 SHA、范围、ref、lexicon 和存储向量绑定；
ANN 回读与原向量的最大绝对差为 `1.043081283569336e-07`。
默认查询仍为 literal，semantic/hybrid 仅显式发现候选。

额外确定性检查包括：真实 Activity 小组件 fresh A/B 的数据库及确定性报告一致；
17 个 Event/Native 输入合同重新生成字节一致；Document scope 重新生成一致；
semantic 输入 metadata fresh A/B 一致，选择规则仅转换来源 namespace。
**未执行全部 35 输入 fresh A/B，也未重新运行两套完整 embedding/ANN A/B。**

## 来源变化的具体适配

- Quest/Talk、Voice 和 Codex 的新 source-authored 字段按版本显式解析，独立 oracle
  分别重建；Codex 角色标签读取实际 `textType`，不误用旧 masked 字段。
- AvatarRename 的 `relatedAvatarID` 按原字段保留，不冒称已消失的 `renameID`。
- Document 7.2 使用 `document-7.2-evidence-v2`。Opaque 数组和未证明的 namespace
  仍为 `mapping_unresolved`；已有 Localization 候选可携带来源明确导出的
  Readable 路径、字段坐标与精确 seal。书名—正文关联仍显示为候选，不升级 ownership。
- 五工具 follow 从已核验的 Document 源候选行交付这些路径，并严格回读匹配的
  Readable container；错误 seal、错坐标或多义坐标拒绝，不猜目标。
- Photo root/page selector 的物理并集为 24 个位置引用，仍明确 partial。
  240 个无正文 Talk 终端记录保留 unresolved，不生成对白、speaker 或剧情顺序。
- 旧 query/inspect 工具名保持兼容；执行版本由 exact profile/snapshot 决定，
  不能由名称中的 `71` 推断实际读了哪份数据。

## 失败、恢复与运行时资格

原始失败及中断没有改写为成功：

| 记录 | 真实结果与处理 |
| --- | --- |
| 初轮组件诊断 | 修复 core 游戏版本变量覆盖、QN/Review/Native 旧分母和实际来源范围差异；失败记录保留，随后按新固定范围独立核对。 |
| 初轮 R0 外层阶段 | 来源 oracle 已通过，但构建期间共享代码变化，阶段正确拒绝签发成功回执；冻结后重建。 |
| C/D 完整尝试 | 发现 checker/preview 仍固定旧版本后主动中断，分别修复版本选择；不计成功。 |
| E 完整命令 | 35 输入与两次索引 oracle 通过，但 runtime smoke 失败，32:17.34、exit 1；semantic 未运行。修复 proof ceiling 元数据、Codex 标签和 Book 正文交付。 |
| F 首次完整命令 | Readable 消费者仍要求旧 Document v1，16:24.67、exit 1。原目录/回执保留，新建 Document 阶段，精确更新 v2 消费门禁。 |
| F 继续执行 | 在代码/输入身份匹配时复用未受影响阶段，通过正常阶段入口完成 Readable、Event、Native、索引和新 semantic；没有重签失效回执。 |
| 补充五工具检查 | 首次 Book follow 将合法 `raw_target=NULL` 当 JSON 解析而失败。最终仅修复运行时，从精确源候选行取附加 metadata；原失败保留，定向及真实 MCP 复验通过。 |

F 的数据、完整 source/index/semantic oracle 在
`0ddad7ca9f3c72104b5243fde21cd5cbe39da513` 对应实现下完成资格核对。
最后一项是纯运行时修复，未改变 parser、投影、SQL、索引或向量。
为它另外生成了 versioned runtime profiles/qualifications，复用原 exact artifacts，
**原 profile、v3 数据回执和 seal 全部保留不变**；旧 runtime profile 会拒绝新的代码。

最终实际核验入口为输出目录中的：

- `runtime-config.readthrough-literal.json`
- `runtime-config.readthrough-semantic.json`

新版本 profile 按既有机制核验本次运行时代码、同一 corpus/bundle/index 及来源绑定；
没有关闭门禁或使用旧 accepted receipt。更广的 v3 build fingerprint 对共享运行时代码
变化仍保守拒绝自动恢复。这不等于旧数据失去原来源证明，也不允许重签旧阶段。
**最终 runtime-only 修复之后未再执行一次 35 输入冷构建**，不将分阶段恢复、
独立运行时资格和首次单命令成功混为一谈。

可重建冗余数据库按本地精确清理回执退出留存；完整失败日志、报告、seals 和必要
witness 保留。没有清理正式 7.1、固定来源、legacy 输入或其他 WIP。

## 实际 MCP 验证

使用真实 stdio ClientSession 调用生成配置，未调用语言模型：

- 五工具 exact discovery 及 search/read/follow/inspect 均通过；独立便利 surface
  仍为 query/inspect 两工具，没有新增顶层工具。
- literal、lexical、semantic、hybrid 均返回可正式 read/inspect 的 7.2 ref。
- 中文单字、标点、英文查询及下一页无重复；错误 corpus 拒绝。
- Book 候选导航的 `resolve_targets=false` 不展开，true 交付可读可 inspect 的 CHS
  container，仍为 partial/physical candidate。
- 《终北祷歌·上》候选文件读了 13 页，覆盖物理行 0..84 / 共 85 行并到当前文件 EOF。
- 胡桃一篇角色故事从首次 400 字符后续读至 467，range inspect 和 segment/full SHA 一致。
  这是当前故事 observation 的 EOF，不是角色全部故事。
- 人物对白连续两页、正文/provenance、错误 cursor 拒绝与阅读卡回读均通过。
- 补充 Voice、Quest、NPC 发言及 Event 正文路径读取并 inspect；Event 测试材料仍只提供有限结果。

便利 query 首次收到“7.2凯瑟琳有哪些相关对白？”时未识别主体，返回无材料。
其 intent/subject 解析函数与 main 基线 AST 完全相同，固定 7.1 也有该限制；
这不是 source 缺失或五工具人物对白失败。原请求失败保留，支持问法的补充探针不冒充原题通过。
研究 Skill 已按维护决定强调提取主体关键词、优先五工具和读取上下文，不扩建问句解析器。

## 回归、资源和未执行范围

- 最终全量合成回归：**827 passed, 3 skipped**，175.16 秒。
- 最后运行时/续读/依赖门禁定向回归：43 passed。
- Skill validator、11 项文档/布局检查、源码编译、六个公开命令 help 通过。
- 实际数据/向量及所有历史失败记录留在 ignored 本地目录，Git 仅提交源码、静态合同和脱敏摘要。
- 当前项目 hard 为 90,000,000,000 B，warning 为 77,309,411,328 B；最终同口径容量、
  主机空闲及 unknown 项列于 JSON。成功 quiet pytest 未打印的临时目录也单独归属并计入，
  未把主机空闲当作项目预算。该记录是时点实测，不是连续全程峰值。
- 阶段时间可核对 JSON；本轮存在失败、恢复和并行回归，不宣称冷构建提速。
- 自主模型最终答案、原生 Windows 7.2 完整来源构建、正式 release promotion：**NOT_RUN**。
  本次程序化 MCP 成功不冒充真实模型自主研究或产品发布验收。
- 静态 diff 自检完成；独立 PR 审核仍待进行，不冒称已有独立 reviewer 结论。

## 重建与使用

冷构建采用 [完整重建指南](../guides/building-dataset.md#显式构建-72) 的固定来源、
历史 oracle 来源和本地模型，显式 `--source-version 7.2` 并选新输出目录。
当前源码会在构建时绑定修复后的运行时，正常生成 `runtime-config.json`；
不复用本次旧 fingerprint 回执，不复制数据库，不把历史成功当作新环境验收。

本次保留数据上的最终运行资格可通过以下方式使用；将 `OUTPUT_ROOT` 替换为该输出目录：

```sh
.venv/bin/python scripts/serve.py --config OUTPUT_ROOT/runtime-config.readthrough-semantic.json --check
.venv/bin/python scripts/serve.py --config OUTPUT_ROOT/runtime-config.readthrough-semantic.json
```

这不会修改客户端默认。需要连接 7.2 时显式配置上述 stdio 命令；未命中不回退 7.1。
原 7.1 部署和源码历史记录仍按各自 identity 解释。
