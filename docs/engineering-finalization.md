# 工程整改交付记录

本记录汇总公开源码的分层整改，不是数据库或正式 release 验收。基线为 main `e75aef63acbc3a516d45a124e66dc70bf20c263b`；实现核验至 `ecf31c96f3b29c646b16d94dbbf09092aad00c63`。Apache-2.0、NOTICE、既有原文、证据资格及部署 current 均未改变。

## 待审核的 PR

以下 PR 依次堆叠，各自保留独立 diff；均未合并，不应并行按 main diff 重复审核全部父提交。

| PR | 范围 |
|---|---|
| [#1](https://github.com/Andrew05213/genshin-lore-research/pull/1) | 严格验证状态、恢复身份、输出隔离、独立 oracle、CI 与 SDK 兼容封装 |
| [#2](https://github.com/Andrew05213/genshin-lore-research/pull/2) | 独立审查四项缺陷，以及四次真实重建暴露的报告/路径/投影接线修复 |
| [#3](https://github.com/Andrew05213/genshin-lore-research/pull/3) | 当前构建和查询模块的可读性、命名常量与维护说明 |
| [#4](https://github.com/Andrew05213/genshin-lore-research/pull/4) | Windows 锁、文件持久化、路径、固定依赖与原生 CI |
| [#5](https://github.com/Andrew05213/genshin-lore-research/pull/5) | core 合同回填减少重复扫描、行集等价测试及阶段耗时 |
| [#6](https://github.com/Andrew05213/genshin-lore-research/pull/6) | 默认完整构建包含向量、独立完整来源 oracle、固定模型及生成运行配置 |

## 实际修改与保留边界

- 构建成功须有明确的完整执行结果；未知、空、跳过、失败及冲突结果不能获得成功回执。恢复绑定实现、合同、输入、依赖、环境及报告 seals，不因文档变化触发重建。
- 每次输出拥有自己的动态 metadata/profile/receipt。没有第二套调度框架、缓存服务、自动依赖追踪系统或新的顶层 MCP 工具。
- 只整理当前维护的构建与查询代码，不批量重命名历史 parser/run、删除来源清单或合并独立 oracle。依赖调查未证明无约束且不可达的模块，不据此删除。
- Windows 使用原生文件锁与实际协议 CI；POSIX 保留已验证的传输。私有 SDK 依赖继续集中在兼容边界，不散入业务模块。
- core 回填使用临时精确 parser/run 查表，保留 source_key，不合并物理证据。完整合成行集与旧算法相同；永久 schema 不变。
- 默认 `--stage all` 包含独立 semantic overlay，显式 `--without-semantic` 保留基础模式。搜索仍默认 literal。模型先显式取得并核验，构建不隐式下载，不复用旧部署向量。
- semantic oracle 独立枚举全部应有来源与窗口，核对 ref、正文/窗口 SHA、范围、向量和 lexical 行集；不能仅信任 builder 的计数或重新封印后的残缺映射。
- 默认配置是可变输出指针，不是 immutable receipt。先核验独立 semantic 配置，再只替换精确匹配本次基础输出的指针，保留 literal 配置；未知配置拒绝覆盖。

详见 [维护范围](engineering-maintenance.md)、[Windows 指南](guides/windows.md) 和 [向量构建](guides/building-semantic.md)。

## 本次执行结果

| 检查 | 结果与实际范围 |
|---|---|
| Python 3.12 干净环境 | PASS：hash 锁定开发依赖、editable 源码安装；不依赖私人数据库 |
| 全部无游戏数据测试 | PASS：200 项，Linux 本地 30.53 秒；包含状态/恢复负测、双输出隔离、真实 stdio 五工具请求、对白分页/cursor、引用及配置切换 |
| 托管 CI | PASS：[实现提交的 Ubuntu / Windows 运行](https://github.com/Andrew05213/genshin-lore-research/actions/runs/39088216805)；文档提交后的状态以各 PR checks 为准 |
| 覆盖率 | PASS：所选构建调度、资格、配置、MCP 和对白模块 70%（961 statements）；不是全仓库覆盖率，子进程执行不完全计入 |
| 编译、六入口 help、Markdown、许可 | PASS：本次实际执行；六入口为 build/build_component/serve/check/serve_dialogue/check_dialogue |
| wheel | PASS：构建、名称、Apache-2.0、LICENSE/NOTICE 及无数据文件检查；仍采用源码 checkout 分发，不宣称独立 wheel 包含所有 SQL/recipe |
| 当前实现 Activity 来源 A/B | PASS：从已有干净固定来源独立构建两次，来源 oracle 与严格 compare 均通过；每份 94,208 B，SHA-256 `af3a106331df8cb37c455f0b57f6af951dee14951f3f3a48a1b9cc498ccea508` |
| 固定真实 ONNX 模型 A/B | PASS：合成文本的两个物理 occurrence、每次四个窗口；完整来源 oracle、向量值/输入身份及运行时查询通过；不是游戏文本语义质量或全量向量验收 |
| 修改后完整 35 输入 / 全游戏范围向量 | NOT_RUN：项目新增峰值额度不足；四次原失败仍保留，不能沿用历史成功 |
| Windows 完整游戏来源构建 | NOT_RUN：原生 CI 验证源码、合成构建与 MCP，不代替真实全量来源验收 |

固定模型为 multilingual MiniLM、384 维 cosine HNSW，revision `faf4aa4225822f3bc6376869cb1164e8e3feedd0`；本次实际运行 FastEmbed 0.7.4、ONNX Runtime 1.30.0、hnswlib 0.8.0。A/B 输入行集 SHA 为 `c7e6a9a9d0c7fa871a7768647fbd83c8f1adb368e9c27a9771eb2a55dfe2eba8`，向量文件 SHA 为 `d3f9024a88f9d0bf7f7bfd69d637083afdc9f41ed66c55e0ce9e4a6e801eb19e`。二次独立运行分别约 2.86/2.63 秒，不作全量性能结论，也不要求 ANN 二进制在所有平台逐字节相同。

core 局部同条件三次对照中位数为 0.49655 → 0.14840 秒，见 [原始数值](history/core-reconciliation-performance.json)。这只是减少重复扫描的局部收益，不是完整数据库构建提速声明。

### 保留的失败与集中 review

- 初次共享环境测试有一次子进程导入其他 checkout，178 通过 / 1 失败；明确源码路径后 179 通过。最终使用新建独立环境执行 200 项。
- Windows 初轮缺 pywin32 lock；后续分别暴露路径分隔符和只读 descriptor 持久化问题。失败运行 [39086943143](https://github.com/Andrew05213/genshin-lore-research/actions/runs/39086943143)、[39087121434](https://github.com/Andrew05213/genshin-lore-research/actions/runs/39087121434)、[39087327442](https://github.com/Andrew05213/genshin-lore-research/actions/runs/39087327442) 保留，修复后原生 CI 通过。
- 集中独立只读 review 实际复现“基础输出追加 semantic 时默认配置冲突”；原测试手工移走配置掩盖了该场景。修复后保留实际旧配置，测试失败 smoke 不切换指针、合法追加、重复恢复与未知配置拒绝。review 复核实现后无剩余 P0/P1 blocker；不代表完整 35 输入通过。
- 真实来源四次失败见 [重试记录](full-rebuild-retry.md)；本次未删除失败目录、替换历史报告或重新签发旧资格。大型日志、数据库和模型仅本地保留，不提交 Git。

## 容量与未执行的高成本门槛

相同项目范围按 device/inode 去重，包括相关 worktrees、来源、临时产物与缓存；无 unknown 项。项目额度与主机空闲分别计量。

| 项目 | 开始 | 结束 |
|---|---:|---:|
| 项目 allocated bytes | 77,210,673,152 | 77,345,718,272 |
| hard 余量 | 12,789,326,848 | 12,654,281,728 |
| 主机可用 bytes | 133,382,209,536 | 133,245,509,632 |

本次约增加 135 MB，主要为独立开发环境、打包及小型验证；没有复制正式 DB/index/model。hard 仍为十进制 90,000,000,000 B，warning 为 77,309,411,328 B；结束略超 warning，未越 hard。RAM 约 16.76 GB、swap 约 4.29 GB；主机空闲和内存级别足够，不能代替项目预算。

默认完整构建当前保守新增峰值为 27,000,000,000 B，距结束时余量还缺 **14,345,718,272 B**；尚未取得的模型、来源或依赖还需另外预算。没有为执行全量测试擅自清理数据或提高限额。

资源满足并取得所需来源后，在新输出目录执行：

```sh
python scripts/build.py --source <pinned-7.1-source> \
  --historical-source <pinned-history-source> --model-cache <verified-model-cache> \
  --output-root data/engineering-full-a --stage all \
  --available-allocated-bytes <fresh-project-headroom-bytes>
python scripts/serve.py --config data/engineering-full-a/runtime-config.json --check
python scripts/check.py --config data/engineering-full-a/runtime-config.json
python scripts/check_dialogue.py --config data/engineering-full-a/runtime-config.json
```

第二次独立构建使用新输出目录，并重新计算同时保留 A/B 与临时峰值，不能复制 A 充当 B。真实固定模型小型 A/B、Activity A/B 和 200 项源码测试已经本次执行；**完整 35 输入、全量游戏向量和新数据正式发布仍待验证**。
