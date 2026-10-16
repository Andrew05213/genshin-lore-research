# PR #1 独立审查（2026-10-16）

## 结论

**Request changes：原头提交尚不具备安全合并条件。** 四项可复现缺陷见下表。本审查补丁只修复这些问题并补充合成回归测试；修改后完整 35 输入重建及新生成运行配置的端到端验收仍为 **NOT_RUN**。应在补丁复核、最终头提交 CI 通过及完整重建验收完成后重新判断合并。

固定审查对象：[PR #1](https://github.com/Andrew05213/genshin-lore-research/pull/1)，头提交 `50c24a279e8d3da7fbdb47d985fe5abf0972abe1`，基线 `e75aef63acbc3a516d45a124e66dc70bf20c263b`。读取差异和工程报告后，在独立 checkout 安装 hash 锁定依赖并复跑测试；没有将原作者报告当成独立执行证据。检查时 PR 开放、未合并、9 次提交、54 个文件、+4738/-339。

## 合并前应修复

| 级别 | 位置（原头提交） | 实际触发与影响 | 最小修复 |
|---|---|---|---|
| P1 | `scripts/validators/quest_review_binding_v2.py:73–85` | `manifest` 先存清单 Path，随后被 build 报告 dict 覆盖，`manifest.read_text()` 必然抛 AttributeError。默认路径和显式新路径均失败；完整构建会停在 `quest_review_binding` 验证阶段。 | 用 `dependency_manifest` 单独保存清单路径，读取和封印均使用它；保留报告对象和全部后续门槛。 |
| P1 | `scripts/validators/research_dataset.py:19–72,266–279` | 验证器只要求组件至少保留一条覆盖声明。真实合成源仍有正文时，删除全部 `research_text` 及其 text 覆盖行，完整 `verify_connection` 返回 `PASS_COMPLETE_SOURCE_ROWSETS`；保留覆盖行但改为 unsupported/0 同样通过。计数与覆盖报告一致，不能证明必需投影存在。此完整性盲区在旧 oracle 中已存在，PR 新增检查尚未关闭。 | 从静态来源合同和独立验证器的表清单确定必需投影，拒绝必需覆盖缺失或降级；再执行原有实际计数和逐行来源核对。显式 dependency_only 与合法 unsupported 关系继续保留。 |
| P2 | `scripts/build.py:91–111` | 将已验证阶段 `original/` 整体复制为 `different/`，不修改回执或封印，`dependency_inputs('different')` 仍接受。它按回执自报的 stage 参数重算身份，没有核对调用者要求的阶段名称。下游 oracle 可能再次拒绝错误 schema，但这不能代替依赖资格检查。 | 核对期望阶段名、invocation 阶段名、invocation/receipt identity 和两处 snapshot。 |
| P2 | `scripts/build.py:83–89` | 令 `output/metadata` 为指向另一输出的目录符号链接，调用 `bind('manifests/fixture.json', ...)` 会在另一输出创建文件。只检查 `..` 不足以实现目录隔离；无需覆盖已有文件或并发攻击即可复现。 | 写入前解析目标路径，并要求它位于本次输出根内。此检查针对已有符号链接，不宣称防御恶意并发换链。 |

对应回归测试在 [test_pr1_review.py](../../tests/test_pr1_review.py)。六条负例分别在对应未修复逻辑上复现失败：两种清单调用、阶段改名、两种投影缺失、符号链接越界。未删除原失败目录或原验证条件。

正文投影负例先用实际 Research projector 生成含一条正文的合成索引，确认完整来源验证通过，再执行删除/降级并调用同一个完整来源验证入口；不是仅测试自建计数函数。另有实际内存索引构建、独立 oracle、磁盘 publication、v2 阶段回执和精确恢复的合成正例。

## 工程判断及后续改进

- 成功状态明确集合、冲突状态拒绝、构建前后 artifact seal 和 SQLite sidecar 检查方向正确；核对了调度器实际调用的来源验证器返回合同，没有发现必须扩大成功集合的证据。保持 NOT_RUN/SKIPPED 拒绝。
- 约 3240 行新增来自 `engineering-module-dependencies.json`（2363 行）及依赖锁文件（877 行）。不能仅凭 4738 行判断引入大量运行时抽象。其余代码中，阶段资格规则、路径解析和 MCP 兼容层的提取有具体职责；本次没有发现需要大规模重构才能合并的证据。
- 独立 oracle 必须从自己的来源合同确定分母。本次新增的静态必需表清单刻意不导入 builder 投影函数；这份有限重复用于避免同错，不建议为了去重合并来源重建算法。合同变化时应同步独立核对清单并重跑全量验收。
- MCP 兼容层保留现行传输、锁定 2.1.1，实际 stdio 的发现、请求、对白分页、cursor 错配和引用测试通过。私有 SDK 接口仍是升级维护成本，但当前没有证明其引入新的协议回归，不能据此强制改用另一套未验证传输。
- 源码 checkout 安装策略和 wheel 不包含外部 SQL/recipe 的限制已公开说明；本次没有把 wheel 构建成功当成独立部署可用。直接依赖、开发锁文件和 Python 3.12 CI 一致。
- 可后续改善：将路径/报告变量名进一步区分，减少类似 manifest 覆盖；为高成本阶段增添少量真实合成接线测试；明确维护依赖图的生成方式。依赖图静态快照不能作为删除历史模块的证据。这些不要求在本次补丁中扩建配置框架或通用依赖追踪平台。
- 现有普通目录隔离及配置相对路径测试通过；新增符号链接回归关闭了已证实的 metadata 越界。没有声称审计了任意恶意本地文件系统操作者。

## 独立执行范围

| 检查 | 本次结果 |
|---|---|
| 原 PR 无改动基线 | 129 passed（Python 3.12.14，独立新虚拟环境） |
| 锁定安装 | `pip install --require-hashes -r requirements-dev.lock` 及无依赖 editable 安装通过 |
| 原托管 CI | 直接查询 run `38886268282`，head 为 `50c24a2`，completed/success；不是补丁 CI |
| 最终本地测试 | **138 passed**，含新增 9 项及实际 stdio 子进程测试 |
| 针对性集成 | 合成索引 publication、独立来源 oracle 和精确恢复通过 |
| 编译、六命令 help、差异空白检查 | 通过 |
| wheel | 构建及 Apache-2.0 / LICENSE / NOTICE 元数据检查通过；仅验证源码发行策略 |
| 选定模块覆盖率 | 中间修复版 135 项测试时约 62%；子进程执行不完整计入，不能推断 35 输入接线已覆盖 |
| 真实上游完整 35 输入重建 / 新生成完整运行配置 | **NOT_RUN**；本轮未获取完整游戏来源或复用私人数据库 |

## 完整 35 输入单独验收条件

需要先具备两个完整、干净且与合同一致的固定来源工作树：7.1 commit `9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99`（tree `aa578c08c402af39b41f1ffc95124d10f5410272`），以及 `manifests/quest-derived-7.1/review-field-continuity-v1.json` 声明的历史 commit。最小稀疏来源不满足条件；从零 clone/fetch 全来源仍是另一个需要实际记录的边界。

来源、代码、依赖已就位后，按现有预算合同至少需要 **25,000,000,000 B 新增项目峰值余量**，输出所在文件系统至少 **26,000,000,000 B 实际空闲**，以及约 **16 GB RAM / 4 GB swap** 的既有已验证级别资源。内存索引及 VACUUM 会消耗内存；资源不足应保留失败证据，不能关闭来源检查。上次报告的项目余量 6,529,504,256 B 对应缺口 18,470,495,744 B；这是原报告的预算口径，本审查没有将它冒充当前远端实测值，也没有把本环境磁盘空闲当作该项目额度。

在最终代码固定、CI 通过后，以全新输出运行 [完整指南](../guides/building-dataset.md) 的 `build.py --stage all`，再对本次生成的 `runtime-config.json` 执行：

```sh
python scripts/serve.py --config <new-output>/runtime-config.json --check
python scripts/check.py --config <new-output>/runtime-config.json
python scripts/check_dialogue.py --config <new-output>/runtime-config.json
```

应记录 35 个 exact inputs、33 个 core contracts、每阶段独立来源验证、统一索引的必需投影/实际行集/完整来源核对，以及新配置的两类服务和对白分页/引用结果。不得使用旧回执、历史库 smoke 或本次合成测试代替。任何 NOT_RUN、SKIPPED、身份冲突或未知状态均保持拒绝。

第二臂 B 必须再次从来源构建，不能复制 A；保留 A 与失败证据后重新审计项目及主机峰值余量，不能假设第一次 25 GB 足够同时保留 A/B。比较来源身份、逻辑行集及其 seal；包含绝对路径或执行耗时的元数据不可不加区分地要求字节相等。正式发布接受与来源构建验收仍分别判断。
