# 工程质量与独立验证范围

本次检查以公开仓库 main `e75aef63acbc3a516d45a124e66dc70bf20c263b` 为基线，在独立分支 `fix/build-trust-and-runtime-isolation` 实施。许可证保持 Apache-2.0；未改写历史报告、SQL schema/迁移、游戏原文、既有 release receipt 或 evidence ref。本文不替代来源 oracle 或正式发布资格。

## 实际缺陷及整改

| 问题 | 处理 |
|---|---|
| 验证状态只排除包含 FAIL/BLOCKED/ERROR 的字符串 | `build_support.require_success` 使用实际完整执行合同的明确集合：PASS、PASS_PHYSICAL_PROJECTION、PASS_COMPLETE_SOURCE_ROWSETS，兼容大小写。空结果、未知、NOT_RUN、SKIPPED、PARTIAL 及冲突 status/verdict 拒绝；数据自身的 candidate/unresolved 不升级。 |
| 仅凭数据库 seal 恢复，旧解析/验证代码和损坏报告仍可能复用 | v2 阶段回执绑定实现、静态合同、Python/SQLite/直接依赖版本、来源根、参数、显式依赖及 build/verify/invocation seals。成功回执最后写入；不完整/失败/旧回执拒绝自动恢复，保留诊断。文档不进入身份。 |
| narrative 组件适配可能覆盖已有 status | 只在原 status 缺失时补通用字段，保留并核对原 verdict；A/B 比较同样拒绝冲突状态和 SQLite sidecar。 |
| 独立 Research/semantic 校验依赖 Python assert | 改为显式 ValueError，不因 `python -O` 消失；保留原表达式，没有降低门槛。完整来源 oracle 不调用 builder 的投影函数。 |
| coverage 的 indexed_count 没与实际行集数量闭合 | 独立核对四种投影的实际数量、覆盖声明及重复 exact 物理定位，并以 foreign_key_check 拒绝会在关系 JOIN 中消失的孤立引用；保留逐行正文、坐标、关系、候选和来源核对。不再允许删掉索引行却沿用 unchanged coverage 通过。 |
| 不同 output-root 写同一份源码 manifest，索引模块用全局变量换路径 | 动态配置归属各输出的 metadata/；Character、Core、Quest、Event、Native 和 Research 显式接收本次依赖。Native 不再在 import 时读取机器 manifest。共享静态代码不共享动态绑定。 |
| 五工具服务为传输辅助函数导入整个历史 MCP 服务 | 必要的私有 SDK 操作集中至 mcp_transport.py，明确 mcp==2.1.1；现行工具不再导入历史 provider 服务。工具名、参数、返回及引用语义保持。 |
| 缺少持续集成、安装依赖可漂移 | Python 3.12 PR/main CI、开发传递依赖 hash lock、合成负测、真实 stdio 协议、覆盖率、编译、help、链接/许可和 wheel 检查。semantic 依赖仍可选。 |

原状态缺陷已在原代码上用合成产物实际复现：NOT_RUN、SKIPPED、空字典、UNKNOWN，以及 status=PASS/verdict=NOT_RUN 均错误生成成功回执。新测试针对这些反例拒绝，不删除原失败证据。

实现身份采用保守、可解释的代码/合同文件清单，而不是通用依赖追踪平台。其代价是无关管线代码变化也可能拒绝恢复；需要新输出或明确重验，不根据“文件名看起来相同”复用。回执不宣称对抗能同时重写所有本地证明文件的恶意操作者。

## MCP 兼容策略

在 Python 3.12.3、MCP 2.1.1 / AnyIO 4.15.1 环境中，公开 `MCPServer.run_stdio_async()` 的最小单异步工具 initialize 探针在 15 秒超时。没有据此认定 SDK 在所有环境有缺陷，也没有未经运行验证就替换传输。

因此保留已工作的 pipe I/O 和同步 handler 兼容行为，将 `_claim_fd`、diversion、`_lowlevel_server`、`_tool_manager` 操作集中到单一兼容模块。不同 SDK 版本明确拒绝，升级须重新核验。真实合成子进程覆盖五工具发现/请求、source-checked read/inspect、前驱、分页、错误 cursor、handle 回查和无效 handle；不是仅检查 schema 登记。

保留的真实 7.1 profile 需要可选 semantic 依赖。新基础 CI 环境启动它首次因缺 fastembed 失败；未关闭验证。随后用原有合格依赖环境（MCP 2.1.1、AnyIO 4.15.0）执行两页对白 MCP smoke，通过正文/provenance 等价和错误 cursor 拒绝。该检查复用原正式运行根，不是新代码完整数据构建验收。

## 模块职责与克制的重构

[依赖清单](engineering-module-dependencies.json) 记录源码及脚本的保守 AST 导入边、字面量动态导入和构建配方候选；函数内导入也计入。它不是完整运行轨迹，未解析动态调用明确单列。

| 分类 | 模块数 | 含义 |
|---|---:|---|
| A | 106 | 当前公开 runtime 入口的保守依赖闭包 |
| B | 116 | 当前构建入口额外依赖，包含 family-specific parser/oracle |
| C | 72 | 历史 CLI/pipeline/MCP 入口额外依赖 |
| D | 15 | 独立检查或维护入口额外依赖 |
| E | 0 | 已证明无合同约束且不可达、可删除的模块：无 |
| 未分类 | 98 | 不在上述闭包，仍需动态/合同调查；不等于死代码 |

只提取了传输兼容层、阶段资格与显式路径解析。未批量拆分 1,700+ 行历史 CLI/pipeline、改名 phase/batch 模块或删除来源 manifest/SQL；它们仍有构建或兼容依赖，改动风险高于本次收益。独立 oracle 的少量重复来源规则刻意保留，不因“去重”与 builder 共用投影逻辑。大型来源清单承载身份和完整范围，未缩减分母或用新的配置生成平台代替。

## 本次执行记录

| 检查 | 状态 | 范围 |
|---|---|---|
| Python 3.12 干净环境安装 | PASS | hash 锁定开发依赖、editable source；不读取私人数据库 |
| 原有及新增无游戏数据测试 | PASS | 128 项：验证状态/恢复/失败保留、并行双输出、实际 stdio、对白/cursor/引用、链接与许可；最终 CI 计数以 PR checks 为准 |
| 优化模式负测 | PASS | 非法 Research schema 在普通 Python 和 -O 均拒绝；当前三份 Research 验证脚本无关键 assert |
| 六个公开命令 --help | PASS | build、build_component、serve、check、serve_dialogue、check_dialogue |
| 编译、文档链接、名称/Apache-2.0 | PASS | 当前源码、docs、元数据；历史记录不改写 |
| GitHub Actions 配置与首轮托管执行 | PASS | PR、main push、手动触发；无游戏下载或个人凭据；[首轮托管运行](https://github.com/Andrew05213/genshin-lore-research/actions/runs/38885384385) 全部步骤通过；最终提交状态以 PR checks 为准 |
| wheel 构建及元数据 | PASS | 源码 checkout 分发策略；不宣称 wheel 独立包含 SQL/recipe/manifest |
| 新目录上游获取 | PASS | 从公开上游实际 fetch 固定 commit；稀疏 checkout 七个最小输入，commit/tree/clean 均核验 |
| Activity 新来源 A/B | PASS | 两次实际解析和独立来源验证，94,208 B；SHA-256 `95d9149a8b274cc14d67eeda76aefd973d245df08e280fd14b0dd80abbaea1e1`；最终严格 compare 通过 |
| 真实最小阶段 v2 回执与恢复 | PASS | 最终管线代码再次独立构建 Activity 并来源核验，生成 v2 verified.json；第二次原样调用 REUSE_VERIFIED，没有重跑或借用旧回执 |
| 新实际 rowset 检查应用于真实 7.1 索引 | PASS | 最终四种投影数量、声明、重复定位与外键检查，约 8.42 秒；只读/immutable 连接，原文件 stat 未变；不替代完整来源重建 |
| 保留真实 7.1 对白 MCP | PASS | 五工具、两页、read/inspect 正文/provenance 等价、cursor 错配拒绝；未修改部署 |
| 修改后完整 35-input A/B / 新生成完整运行配置启动 | NOT_RUN | 当前项目预算不足完整构建峰值；合成隔离/MCP 和历史真实部署 smoke 不能冒充此项 |
| 新 semantic 模型/向量重建 | NOT_RUN | 非本次整改范围；不下载权重或重建已有索引 |

最小联网实验是从新目录取得指定范围，而不是独立从零获取全部游戏来源。原 commit 仍可访问不代表上游永久保留。

本次保留了两次环境/接线失败：公开 SDK stdio initialize 超时；脚本路径辅助模块迁移时，合成子进程缺少 repo Python path 导致初始化关闭，修正测试子进程的明确路径后复验通过。另有基础环境缺 semantic extra 的真实启动失败见上文。没有把失败重试写成初轮全过。

本地覆盖率仅针对构建调度、阶段资格、运行路径、MCP 兼容层和对白包；子进程协议测试通过不代表子进程执行行都进入主 pytest coverage。没有为了百分比加入低价值测试或放宽门槛。

## 数据身份与后续高成本验证

没有修改既有数据字节、来源范围、证据状态、SQL 名称、历史 parser/run ID 或正式发布身份。修改后的构建源码和动态依赖路径可能按原有实现 fingerprint 规则生成新的 parser/run/seal；旧成功回执不能自动为新实现背书。未来发布这些新构建产物前，需要重新执行受影响 family oracle 和完整统一索引验证。

本次较早的大范围 inode 去重盘点为 83,050,811,392 B，hard 90,000,000,000 B，余量 6,949,188,608 B；主机可用约 119.49 GB。相同范围复测为 83,470,495,744 B，余量 6,529,504,256 B，主机可用 119,065,415,680 B，无无法读取项。增加约 420 MB，主要为联网最小来源、干净源码/打包验证和小型测试记录；没有复制正式数据库/索引。未进行清理或提高额度。该预算距完整构建的 25 GB 新峰值仍差约 18.47 GB，不能用主机空闲替代项目额度。

具备干净的固定 7.1 与历史来源、匹配依赖、至少 25 GB **项目**新增峰值余量、输出文件系统相应空闲和约 16 GB RAM/4 GB swap 的已验证级别资源后，执行：

```sh
python scripts/build.py --source <pinned-7.1-source> \
  --historical-source <pinned-history-source> --output-root data/engineering-a \
  --available-allocated-bytes <fresh-project-headroom-bytes> --stage all
python scripts/serve.py --config data/engineering-a/runtime-config.json --check
python scripts/check.py --config data/engineering-a/runtime-config.json
python scripts/check_dialogue.py --config data/engineering-a/runtime-config.json
```

第二次独立构建使用新输出目录，并先重新计算同时保留与临时峰值；不得复制 A 充当 B。旧失败目录和历史资格保持，不强删以满足预算。新数据构建成功与正式 release 接受仍分别判断。
