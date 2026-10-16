# 完整重建实验

## 结论

2026-10-08 实际执行：`PASS_COMPLETE_BOUNDED_DATA_REBUILD`。

从冻结的源码 ZIP 解压到新目录，顺序执行 `rebuild_complete.py --stage all`。全部数据由固定来源重新生成，未读取或复制已部署游戏数据库。35 个统一检索输入、Schema20 的 33 个 core contracts、R0 来源归档、统一索引和程序化 MCP 检查均通过。

这里的“完整”指上述有界数据构建范围，不是全游戏覆盖、semantic overlay 重建、原部署 release 凭据复刻或语言模型答案验收。

机器可读结果见 [实验记录](full-rebuild.json)。此前基础组件失败保持于 [历史重建实验](component-rebuild.md)，不改写其当时结论。

## 输入与环境

- 当前来源 commit：`9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99`；tree：`aa578c08c402af39b41f1ffc95124d10f5410272`。
- 历史来源 commit：`26df1dfbdf05a82bbb1d97506859f3e1c40718d8`，仅用于原有 Event/Review 字段连续性 oracle；不是旧数据库。
- 两个已有 Git checkout 均经过固定身份与清洁状态检查。网络 clone/fetch **未重新执行**；不能据此保证上游永远保留这两个 commit。
- Linux，Python 3.12.3，SQLite 3.45.1；NumPy 2.5.3、MCP 2.1.1、jsonschema 4.26.0。
- 测试源码 ZIP SHA-256：`363953e0bf48281d39f9b4377dbaf2cce95998cee3684c1716caf738169f5235`。最终分发补入本页结果；构建、验证和服务实现与测试 ZIP 相同，分发自身以 `PUBLIC_SOURCE_FILES.json` 为准。

## 数据核验

59 个组件/中间产物/来源归档阶段与 1 个索引阶段，共 60 份资格回执。中间阶段数量不等于独立合同或游戏覆盖率。

| 项目 | 实测 |
|---|---:|
| Schema20 core contracts | 33 |
| 统一索引 exact inputs | 35 |
| 35 个输入数据库总字节数 | 6,706,642,944 |
| adapter coverage 声明 | 195 |
| 容器 | 921,382 |
| 文本 | 3,761,013 |
| 关系 | 663,590 |
| 候选 | 446,132 |

复用原独立来源核验器检查字段、原文、坐标、parser/run 与范围；统一索引检查完整文本及 relation/candidate rowset、逐行来源绑定和 FTS 完整性，不以代表性抽样代替全量核对。不支持的关系继续声明 partial/unsupported。

| 产物 | 字节数 | SHA-256 |
|---|---:|---|
| Schema20 core | 1,049,948,160 | `3e1aaf1825f62235c96d74d82f85dd4a4cfd193377f66f24c77948e1fdf5f463` |
| R0 来源归档 | 4,711,841,792 | `216988588bdb059f9ab87d11fb068121b82f1a27ea5df249025315f9e8e2810e` |
| Research index | 6,569,836,544 | `9392b0a9b33e92fc6a0c40b4d121d0b37bb9575900d84617c4bd70467b0356e1` |

新目录的路径绑定会改变部分 metadata、parser/run 与数据库 seal。上述值记录一次实际产物，不作为任意目录或 Python/SQLite 版本必须复制的字节答案；原文、来源身份与完整 rowset 必须各自通过核验。

## 服务与续读

- 真实 stdio MCP 发现并调用 exact 五工具：describe/search/read/follow/inspect。
- 独立便利接口发现 query/inspect 两工具，不混入五工具 surface。
- Book 使用服务器返回的 token 连续读取 12 页，覆盖当前文件物理 ordinal `0..84`（85 行），到达文件 EOF；逐页检查返回的引用。
- Story 通过服务器返回的 token 读取同一 observation 的字符范围 `400..576`，并以对应 range inspect 核对正文及 segment SHA。此前初始摘录为 `0..400`。
- 文件 EOF 与单个 observation 的字符 EOF 分开。Book 检查不宣称所有长行字符均已续读、其他卷册或整本逻辑作品完整；Story 检查不宣称角色全部故事完整。
- 全部调用来自新生成的 artifact/run/index 绑定；没有 legacy fallback，也没有修改个人客户端配置。

这是程序化真实协议检查，不是模型自主工具选择或最终答案质量测试。

## 失败、修复与时间

早期完整探针保留了实际失败：缺少静态 scope/映射、机器固定依赖路径、不同 release 表结构、verifier 参数差异、NumPy 依赖遗漏，以及 MCP 客户端字段适配错误。完整入口补齐本次来源绑定，不复制旧 acceptance。

独立 oracle 的物理坐标查询原被 SQLite 规划为逐次扫描同 family。公开实现仅在已知 source/path/pointer 范围指定已有坐标索引；过滤条件、歧义判断与全量 rowset 断言保持，定向测试比较两种查询结果。

另一次干净实验因容量复核而主动中止并保留记录：初始余量未扣除中途新增产物。修复后按同一 inode 口径扣除本次新增占用，已有恢复输入不重复计费。中止记录不计为成功，最终冻结 ZIP 的新目录完整运行没有失败阶段或恢复复用。

| 最终完整实验 | 秒 |
|---|---:|
| 来源身份检查 | 0.268 |
| 全部构建、独立核验及入口 runtime smoke | 2,549.732 |
| 额外 runtime 检查 | 24.046 |
| stdio MCP 与续读检查 | 36.718 |
| 合计 | 2,610.764 |

索引发布门槛记录可用预算 14,509,661,952 B；最终索引加 16 MiB reserve 通过。构建先在 RAM 完成核对与压实，再发布唯一输出，不生成第二份磁盘索引。上述约 43.5 分钟是单次执行记录，不是性能保证。

## 未证明的部分

- semantic/embedding、lexicon、模型权重及 ANN 索引未重建；不将 literal 服务通过写成所有可选发现模式已就绪。
- 原部署正式 release 的 receipts/current/客户端日常 renderer 未迁入公开包。生成配置是本次来源核验的本地运行身份，不自动晋级正式产品。
- 8 项 historical contract gaps、12 条 component gap records、有限 Event 与候选 owner/speaker/order 边界不因重建消失。
- 未重新联网下载来源，未执行真实模型研究矩阵，未承诺独立 wheel、非 Linux 环境或未验证库版本。
