# 人物对白读取修复：沃雅妮莎案例

执行日期：2026-10-22。公开源码基线 `fd2bd666f04c6622d5b60657e526a7e4d68a6e54`；实现核验至 `0d414709961a87e7c3d42607df3d7e59fc2d4bc3`。使用方法见 [对白阅读指南](../guides/reading-dialogue.md)。本记录不分发游戏正文、个人配置或原始会话载荷。

## 缺口归因

不是原文缺失或 MCP 宕机。固定 7.1 的合格 CodexQuest artifact 已保留姓名标注、同节点正文和标题。NPC→DialogExcel 的反向候选没有覆盖这些任务档案；旧阅读器的 dialogue view 又只接受 Dialog/Talk。实测姓名检索成功，但 NPC ref 的 dialogue 读取返回 `invalid_request: dialogue view requires a declared Dialog or Quest/Talk projection`；普通 physical read 只有姓名，不能交付任务对白。

相关数据已存在于 `codex_root`、`codex_child`、`codex_text_slot` 和 `codex_text_candidate`，无需 ingestion、重建或新索引。原 artifact 为 870,883,328 B，SHA-256 `07015743386f0de1edf39d5cf4c7dc310385b22502e7d6f1b2f878f20868c76e`，原 parser/run 与 source snapshot 不变。

## 最小修改

- 扩展既有 `read_evidence(view="dialogue")`，接受返回的 NPC/name ref，按精确来源姓名发现 Codex `SpeakerKnown` 候选；不是搜索正文中提到姓名的句子。
- 将同一封存 node-like 记录中的正文、来源姓名、同文件/section 标题及前后节点一起交付。引用仍为原正式 text ref，可分别 read/inspect。
- Codex speaker/body ref 可继续读取当前物理片段；NPC 查询有界分页，cursor 绑定 anchor、locale、limit 与姓名证据。EOF 只属于声明的匹配范围或当前 section。
- 复用既有历史结构 locator 定义；新 wrapper 的 `new_wrapper_role_unproven` 等 proof 原样保留。相同姓名不证明唯一 NPC；平行物理档案不去重，不猜 speaker ID、剧情顺序或跨文件身份。
- 保留来源 `itemType` 和文本分组限制；多文本条目不自动拼成连续发言。来源标题用于可读 citation label，未唯一解析时回到准确物理位置，不造标题。
- 每次请求内复用已核验根元数据，不增加持久缓存服务。Research 仍为原五工具，没有新 planner、ontology、数据库、向量或关系框架。

## 本次实际验证

| 检查 | 结果 |
|---|---|
| 全部合成测试 | PASS：244 项，24.54 秒；包含原 228 项和新增 16 项，无游戏正文 fixture |
| 新增负测 | PASS：坏 root/child/slot、缺失已索引正文拒绝；缺 locale/未投影边界明确 unresolved；cursor 隔离、同文不同 ref、姓名提及不冒充发言 |
| 实际安装的项目 MCP | PASS：读取项目真实配置，沃雅妮莎/阿罗夏均取得正文、标题、相邻文本；分页、scoped handles、原 read/inspect 及相对回执目录验证通过，工具仍 exact five |
| 原 Dialog/Talk | PASS：旧来源角色、图读取、前驱和 cursor 检查 |
| Book/Story | PASS：公开重建配置的原 MCP 全文续读与范围 inspect 回归；不冒称重新重建数据 |
| Sol 日常入口 | PASS：Codex CLI 0.163.0，请求 `gpt-6.1-sol` / medium；仅自然问题，自主 search→read→inspect→answer，17 calls，107.05 秒，实际 MCP 累计约 7.06 秒 |
| 引用交付与答案对照 | PASS：13 条最终引用完成 read/inspect 和 exact handoff；保留条件句、说话者标注、来源标题和相邻文本边界；区分本人标注与他人提及，没有补写剧情 |
| 泛化检查 | PASS（有界）：阿罗夏 3 页、6 个来源姓名 occurrence、6 个不同正文 ref、26 个正式回读/inspect ref；仍有后文，不声称查全 |
| 数据与发布 | 未改 current、profile、registry、receipt 或原 artifact；未重建数据库/索引、未晋级新 release |

同口径项目 allocated 从 81,224,413,184 B 到 81,274,322,944 B，增加 49,909,760 B；hard 仍为 90,000,000,000 B，余量 8,725,677,056 B。没有删除或复制大型产物。原工作区 WIP 状态未变；仅两份本地项目配置切换维护代码路径，保留原值备份，不随 Git 分发。

源档案中的沃雅妮莎姓名标注涉及 15 个物理文件、597 个 occurrence，包含并行结构和重叠材料；**不是 597 条唯一台词，也不是角色全游戏覆盖率**。本地批量诊断完成来源行集比较及正式回读后，汇总文件因同名旧回执存在而拒绝覆盖，最终进程失败。该错误和原始日志保留，未签发全局成功回执；此处不把该失败的诊断进程写成整体 PASS。功能验收依据是另行成功的真实 MCP、Sol 交付和定向测试。

## 保留的失败与修复

- 首轮真实 Sol 回答已完成并自主 inspect，但诊断命令传入相对记录目录，维护服务器与 renderer 的工作目录不同，引用回执定位失败。没有自动修复模型引用或把失败包装成成功。
- 后续同一问题改用绝对记录目录，真实交付为 `DELIVERED`。另外在既有 `dialogue_daily.py` handoff 中修正相对回执目录：只在切换服务器 cwd 前锚定到客户端项目，绝对路径及其他服务器不改。新单测和实际安装 MCP 检查通过。
- 一个合成测试最初将 limit 误当作保证返回数量，触及既有字节预算；改为使用真实 cursor 读至 EOF，仍断言全部七个不同物理 ref，不提高预算或放宽计数。
- 一次只读批量诊断主动中断以检查重复根记录读取；失败堆栈保留。不会用该运行作为完成证明。

本地保留的原始证据摘要（原始载荷不随 Git 分发）：

| 本地证据标识 | SHA-256 |
|---|---|
| `model-daily-absolute/turn-1.json` | `83021209632e03d40f31bf641fd896942eb056a4b67bc14611459f40ee2cc101` |
| `model-daily-absolute/turn-1.delivery.json` | `6e0df817564d613bfe706aa7b65c1dcc530d5047098633a4964749ba8b991d83` |
| `mcp-summary.json` | `6fc8ff529aa70641f30663075083a17231f75de5b3153dedf7fd90f667af5fe5` |
| `generic-check.json` | `f646acd6f3535e36a9bcce3f4ce369b40f557374bbf628905a726dbd55515450` |
| `book-story-regression.json` | `c23d733bb79ab45e6bfae0f7b28a34dd96cd7cceea9a9470aa14c0317b905b79` |

## 部署与剩余边界

本地项目连接和原 `research71.py` 日常入口均指向独立、固定的维护代码 checkout，原 sealed v1.1 仍先正常核验；未改全局客户端设置。原维护代码、配置备份和数据保留，新会话生效，已经启动的 MCP 不热替换代码。公共源码不分发个人部署配置。

正式 authority 仍为 `research71-bounded-v1.1`，新增阅读视图属于维护扩展，不冒称新 release 已接受。姓名匹配只是候选发现；匿名、别名、未映射来源及所有 NPC 发言的完整性仍未证明。8 项历史合同差异及其组件记录不因本修复关闭。原生 Codex TUI/Desktop 不自动获得项目入口的 renderer 保障。
