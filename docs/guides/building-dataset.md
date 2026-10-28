# 完整有界数据重建

`scripts/build.py` 将固定来源重新解析为 35 个独立输入、Schema20 的 33 个 core providers 和统一 Research 索引。它不使用已部署数据库，不复制历史验收凭据，也不生成全游戏覆盖声明。

历史完整执行结果见 [完整重建实验](../history/full-rebuild.md)。工程整改后的 Linux 完整重建与后续受影响组件验证见 [当前性能记录](../linux-build-performance.md)，此前的整改与未通过范围见 [工程质量记录](../engineering-quality.md)；不同实现和运行的结果分别记录，不沿用历史成功。原 14 类基础组件入口和失败记录仍保留，不能代替完整链路的结果。

## 输入

安装方式及 7.1 源码取得方式见 [基础重建指南](building-components.md)。另需历史来源快照，仅供原有 Event/Review 字段连续性 oracle 核对：

```sh
git -C data/AnimeGameData worktree add --detach ../AnimeGameData-history \
  26df1dfbdf05a82bbb1d97506859f3e1c40718d8
```

两个 checkout 都须保持干净。历史源码不是旧版数据库，不参与当前游戏文本查询；不能用 7.1 源码替代它而跳过原校验。

## 容量

先统计整个项目、相关来源、输出和缓存的 allocated bytes，按 device/inode 去重。当前政策为 hard `90,000,000,000 B`，warning `77,309,411,328 B`。hard headroom 是 hard 减去当前项目占用，不能用主机空闲空间代替。

默认含向量的全链路要求至少 28 GB 新增峰值预算；显式 `--without-semantic` 的基础数据构建要求 26 GB。完整固定来源测量中的索引压缩峰值保留约为 25.78 GB，因此不再采用此前的 25 GB 基础预检值。另检查输出文件系统的可用空间。该预算不包含尚未取得的上游 checkout、依赖或模型下载。统一索引构建及完整 oracle 使用较多 RAM；当前验证环境有约 16 GB RAM 和 4 GB swap。增加并行构建会改变峰值，不属于此入口的容量保证。

Character 构建器与独立 oracle 各自按关系一次读取候选行，保留空集合、多候选及所有物理身份核对；foundation 的独立来源核验仍在每次资格检查时重放。M3/special 的 TextMap 查找可以仅为所需 key 生成 lexical hash，但仍完整解析所有词条，检查类型、重复 key、分隔符与尾随内容；完整物理 TextMap 扫描保持原行为。

部分独立 oracle 和 Character 已封存输入使用有界 SQLite mmap，只作用于 `mode=ro&immutable=1` 的只读连接，上限为每库 2 GiB，并继续执行完整性、外键、来源行集和字节 seal 核验。存在 WAL、SHM 或 journal 时拒绝读取并保留现场。SQLite 可以按平台限制缩小或禁用映射，普通读取仍执行相同核验。写入、VACUUM 和发布使用原有连接；Windows 下不对仍需截断或替换的数据库启用此读取方式。

Research 的适用父项和正文投影使用流式 `executemany`，按原顺序逐行消费来源，不累积整表待写列表；父项查询按最多 512 个来源行分批。每次批量写入完成后才检查 coverage 或运行后续投影，全部物理身份、候选、SQLite 约束和完整独立 oracle 保留。512 行限制不是进程总内存上限，单行正文大小仍影响内存。构建报告的 `phase_seconds` 分别记录投影、FTS、独立 oracle、压缩和发布耗时，`component_seconds` 记录各输入的投影及 seal 检查耗时。

Research 在目标目录创建独占的 `.research-*.sqlite` 私有磁盘库；SQLite 主缓存为 16 MiB、临时库缓存为 8 MiB，关闭写库 mmap，临时工作使用文件。主库 `max_page_count` 根据本阶段剩余额度限制，为两个数据库镜像和 16 MiB reserve 留出压缩空间；压缩前再次核对页数及输出/系统临时卷空闲。保留原 `journal_mode=OFF` 与 `synchronous=FULL`，不引入未计入预算的全库回滚镜像。这些是 SQLite 缓存和页数限制，不是整个 Python 进程或独立 oracle 的 RAM 上限，仍需监测实际资源。

投影边界、FTS、oracle、VACUUM 和发布事件逐条刷盘到 `index-oracles/index-staging-*.jsonl`。完整 oracle 首次返回后立即持久化；旧 `index-memory-oracle-*.json` 文件名保留兼容，但现在核验的是磁盘 staging。必需 VACUUM 完成后，再执行同一完整独立 oracle，将结果刷盘到 `index-post-vacuum-oracle-*.json`，关闭连接并刷盘后才以禁止覆盖的重命名发布。失败时保留已创建的私有/已发布数据库、现存 sidecar 和诊断，不以删除现场回滚；SQLite 关闭连接仍可能回滚未提交事务，失败字节不自动具备恢复或查询资格。

## 构建

先运行无游戏数据测试，再执行高成本完整构建：

```sh
.venv/bin/python -m pytest -q
```

其中 `tests/test_research_pipeline.py` 使用微型合成 SQLite 输入，实际贯通阶段回执、
索引 builder、独立完整 oracle、新配置及五工具 MCP 子进程；还检查空 scope、
unsupported 保留、声明遗漏、虚报 complete、搜索分页和两输出隔离。
Event/Native 的实际调度签名与动态路径另由编排测试检查。
这些检查适合修改后的第一轮反馈，不代替真实来源的全量正文/关系验证。
完整重建是最后的来源验收，不应当用来逐个发现参数、状态和声明接线错误。

以下 `HEADROOM_BYTES` 应填入刚完成的项目容量实测余量：

先完成 [固定模型与 semantic 依赖准备](building-semantic.md)。构建过程不自动下载模型：

```sh
.venv/bin/python scripts/build.py \
  --source data/AnimeGameData \
  --historical-source data/AnimeGameData-history \
  --output-root data/rebuilt \
  --model-cache data/model-cache \
  --available-allocated-bytes HEADROOM_BYTES \
  --stage all
```

构建顺序为 core、Character、NPC/Dialog/Reminder、独立文本组件、Quest/Talk、Codex、R0、Readable/Subtitle、Event、Native、统一索引和向量 overlay。仅需基础数据时显式加 `--without-semantic`，不需要模型参数。生成的依赖绑定只引用本次输出；来源读取失败、scope 漂移或 seal 错配时停止，不寻找其他部署的数据库。

每个阶段保留 `invocation.json`、`build.json`、`verify.json`、`verified.json` 与 artifact。只有明确成功的独立验证结果才能创建最后的成功回执；NOT_RUN、SKIPPED、PARTIAL、空结果、未知或冲突状态均拒绝。失败结果和诊断保留，不覆盖旧证据。

v3 阶段回执绑定 Python/SQLite/直接依赖版本、阶段代码域、SQL、静态 manifest/recipe、显式依赖与参数，以及三个报告文件的 size/SHA。恢复逐项检查，并拒绝 SQLite sidecar。未核验、失败、损坏或旧版回执不能自动恢复，必须保留原目录并使用新输出。代码域在 `recipes/build-code-scopes.json` 明确列出；未分类实现和全部静态合同仍共享，未知阶段绑定全部实现。v2 不自动转签为 v3。明确隔离的对白运行时代码变化不使数据阶段失效；无关文档、测试和 Git commit 变化不使回执失效。

Character 后续组件通过本次 metadata 中显式的 `local-character-foundation-v1` 合同消费新 foundation：检查本次 artifact、完整 v3 回执、报告 seal、实现/环境和来源 scope，并重跑原独立 foundation 来源 oracle，要求结果与已封存报告一致。text/lore/voice 的 release 和 run 身份绑定本次 foundation 的实际 size/SHA，lore/voice 的 text 依赖也须精确匹配该 foundation。没有本地合同的历史路径继续要求原固定 artifact seal；不将新数据库写成历史 artifact，也不修改历史凭据。

Native Message 属于 `--stage quest`：先完成本次 Quest/Talk、QuestNarrative 和独立 oracle，再在 metadata 中生成 `local-native-message-quest-narrative-v1` 绑定。消费时核对这两个阶段的当前 v3 回执、完整显式输入、实现/环境和 artifact/report seal，重跑 QuestNarrative 独立来源 oracle；MainQuest 候选集从本次目标 occurrence 与消息原始 locator 完整重建。历史 message scope 的路径、parser/run 与 seal 原样保留，不作为新构建的依赖。

Readable 与 Subtitle 在消费 R0 前先核对本次完整 v3 依赖回执，再将精确 R0 size/SHA 写入本次 `metadata/` 下的作用域。其来源范围和 run 约束保留，builder 与独立 verifier 使用同一份作用域并继续精确核验；该动态文件也纳入阶段输入 seal。源码中的历史作用域及字节 seal 不改写，未核验、漂移或其他输出的依赖回执不能用来生成当前绑定。

动态文件不再写入源码树：

```text
data/rebuilt/
├── <component>/artifact.sqlite + build/verify/invocation/verified.json
├── bound-scopes/
├── metadata/manifests/          # 此次 bundle/profile/registry/资格绑定
├── complete-bindings.json       # 此次 exact artifacts 的显式本地路径
├── index-oracles/
├── semantic/                    # 向量、映射、独立 oracle 与专用成功回执
├── runtime-config.literal.json  # 默认完整构建中的基础数据显式入口
└── runtime-config.json
```

运行配置的四个文件路径相对于 `runtime-config.json` 解析，不相对于调用者工作目录；既有绝对路径仍兼容。artifact 和来源身份绑定保留显式本地路径，不能只移动配置后猜测新数据位置。输出间不共享动态 manifest，不修改模块全局变量指向其他构建。两套小型输出已做并行隔离测试；大型构建仍须按实际同时峰值逐次安排。

`--stage` 可用于定位阶段，但不是完整通过的替代。原始失败目录须保留；不能覆盖旧 artifact 或删除失败报告后宣称首次通过。

## 启动

成功后生成 `data/rebuilt/runtime-config.json`，并执行 runtime smoke。还可单独检查或启动 stdio MCP：

```sh
.venv/bin/python scripts/serve.py \
  --config data/rebuilt/runtime-config.json --check

.venv/bin/python scripts/serve.py \
  --config data/rebuilt/runtime-config.json
```

默认 surface 复用原五工具：describe/search/read/follow/inspect。加 `--surface narrative` 复用独立 query/inspect 便利接口及 Book/Story 续读。客户端通过上述 stdio 命令连接；源码安装和数据构建不自动修改个人 Codex 配置。

五工具请求使用生成 profile 中的 `corpus_ref`。此来源核验入口尚未晋级为正式 release；便利接口的自然问题应明确写出 `7.1`，如“7.1这本《终北祷歌·上》讲什么？”。它不提供其他版本，也不继承原部署的日常默认配置。

生成配置绑定本次 exact artifacts、parser/run、来源归档和索引。其 `accepted` 历史字段仅满足已有 prepublication 协议，`product_accepted=false`，不是正式 release 晋级，也不是复制原部署的 acceptance。

程序化检查可启动真实 stdio 客户端，验证工具发现、五工具调用和 Book/Story 续读：

```sh
.venv/bin/python scripts/check.py \
  --config data/rebuilt/runtime-config.json
```

该检查不调用语言模型，不代表模型自主研究或最终答案质量验收。

## 范围

- 重建的是固定 7.1 的有界数据产品及 literal Research 服务，不是全部游戏数据。
- 8 项 historical contract gaps 和 12 条 component gap records 不因重建消失；候选、未判明关系及有限 Event 范围保留。
- 默认完整入口包括独立 semantic overlay。`--without-semantic` 或单独 `--stage index` 仅构建基础数据；不能将 literal 启动成功写成 semantic 已验证。向量失败时保留基础数据配置，但不交付一个冒充完整向量构建的默认配置。
- 新来源绑定和代码会产生新的 parser/run 与 seal。完整来源正文和坐标核验，比复写旧数据库 SHA 更重要；不声称跨目录、跨 Python/SQLite 版本的数据库字节完全相同。
- 生成数据库、来源正文和包含路径的绑定均不提交 Git。静态 scope 与重建 recipe 保持 tracked；本次运行配置为 ignored 本地产物。
