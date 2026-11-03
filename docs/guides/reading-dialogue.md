# 对白上下文读取

## 范围与启动

对白阅读扩展复用已重建的有界 7.1 数据与统一索引，不生成新数据库。它是显式启用的源码运行路径，不修改已部署的封存 release、current 或客户端全局配置。

完整重建后，使用生成的配置启动 stdio MCP：

```sh
.venv/bin/python scripts/serve_dialogue.py --config data/rebuilt/runtime-config.json
```

工具仍为 `describe_corpus`、`search_evidence`、`read_evidence`、`follow_evidence`、`inspect_evidence`。连接参数指向该脚本与实际生成的配置；不得用文件名推测其他数据实例。启动仍核验 bundle、profile、输入 artifact、索引与来源绑定，`prepublication` 状态不表示正式发布接受。

`describe_corpus` 默认只返回简明语料身份、模式、范围限制及组件摘要；`detail="full"` 返回原完整覆盖信息。这里只精简交付，不改变资格验证或检索范围。参数错误、缺失依赖及 partial 仍明确返回。

Codex 的单个项目连接示意如下，替换全部占位符后使用；不要覆盖其他服务器或全局设置：

```toml
[mcp_servers.genshin-lore-research71]
command = "<venv-python>"
args = ["<repo>/scripts/serve_dialogue.py", "--config", "<runtime-config.json>"]
enabled = true
required = true
startup_timeout_sec = 90
tool_timeout_sec = 60
```

`required=true` 使模型等待来源资格核验和工具发现完成。可选服务配合零等待启动策略可能未进入首轮工具集合；不能把该现象误判为文本不存在。只写 `enabled=false` 而没有 transport/command 的空服务器表也不是有效配置。

已经保留完整合格 v1.1 部署的环境，还可显式使用原 release 数据：

```sh
.venv/bin/python scripts/serve_dialogue.py --config <release-runtime-config.json> \
  --released-runtime-root <retained-release-root>
```

该方式先运行保留 release 自身的正式启动资格检查，再通过只读维护 adapter 委托原 search/read/follow/inspect，并增加上述阅读视图。不会修改封存代码、receipt 或 current；原五工具结果和 exact refs 沿原来源解释。`describe_corpus.dialogue_reading_extension` 明确标为 opt-in、尚未晋级 release。配置错配时直接失败，不改用重建候选。公共源码没有随包包含该部署，不能凭此参数创建正式 release。

保留 release 模式通过独立脚本进程启动；不支持在已经导入另一版本 `genshin_lore` 的 Python 进程中混载。这样避免 Python 模块缓存把指定代码根换成另一份实现。

项目日常启动器可复用 `scripts/dialogue_daily.py` 的命令 handoff。在本地 `research-daily.json` 设置绝对路径 `dialogue_runtime_root` 后，调用 `settings(project, config_path, overrides)`，或以 `capture_wrapper` 包装已有 capture。它只替换五工具服务器的启动命令，保留原问题、resume 参数、引用回执目录和原 renderer，不替换 task 便利服务器。`scripts/serve_dialogue.py` 同时接受既有 `GENSHIN_CITATION_HANDOFF_DIR` 环境配置。配置与备份留在部署环境，不提交个人路径。

当前日常集成不组合旧 compact-context 传输实验；请求两者时明确拒绝，不悄悄改用旧阅读路径。恢复原入口时还原项目 MCP 参数，并移除本地 `dialogue_runtime_root`；不要改数据库或发布 receipt。

程序化验证：

```sh
.venv/bin/python scripts/serve_dialogue.py --config data/rebuilt/runtime-config.json --check
.venv/bin/python scripts/check_dialogue.py --config data/rebuilt/runtime-config.json
```

已存在的 `scripts/serve.py`、query/inspect 便利接口及 Book/Story 续读保持兼容。此扩展没有更换 semantic 模型、重建索引或扩大数据覆盖。

## 按人物查任务档案中的发言

以姓名关键词搜索，选返回的 NPC 或其姓名 ref，调用 `read_evidence(view="dialogue", locale="CHS", limit=...)`。该读取视图使用已有 CodexQuest 的 `SpeakerKnown` 来源标注，分页返回同物理节点中的正文、姓名、任务/片段标题及前后各一个来源节点。每条材料保留原 exact ref、proof 和 provenance；选择正文 ref 再以同一 view 读取，可继续该物理片段。

这里不是对全文中“提到姓名”的简单搜索，也不是按相同 ID 推断说话者。跨 NPC 与档案的相同姓名仅用来发现候选；每条结果显示同节点来源标注，以及新 wrapper 的未证明角色状态。不同 NPC 物理记录、同文的不同档案版本均不自动合并。

此路径只枚举已索引 CodexQuest 中该 locale 的明确姓名标注，不承诺全部 NPC 发言。DialogExcel 的明确 `talkRole` 路径继续通过既有 follow/read 使用；匿名、别名及未映射来源不由此补齐。`scope_complete` 仅指本次来源姓名匹配范围分页结束，不是角色全台词或游戏 corpus 完整。

正文位于 `nodes[].texts`，来源姓名位于 `speaker.names`，可读标题与前后文位于 `source_context`。前后文是同一物理 section 数组的顺序，不证明播放顺序、分支顺序或剧情时间。标题、姓名、正文均可分别 read/inspect，不用路径或节点编号代替引用。

`view="dialogue"` 的 `before/after` 保持 0，前后文已在返回结构内；分页复用 cursor。`source_item_type` 保留可见的来源类型（如 `MultiDialog`），多个文本条目不应自动拼成连续发言。引用标签采用回读的来源标题，内部编号留在来源详情。

## 从搜索到阅读

1. 以主体或线索关键词搜索；按 discovery 声明的类型缩小范围。名称、家具说明和任务描述命中不自动成为对白。
2. 以实际返回的 `evidence_ref` 读取。默认 `view="physical"` 保持原单容器、`before/after` 含义；声明的 Dialog/Talk 记录额外交付 `dialogue_source`。
3. 需要相连对白时，调用 `read_evidence`，保留该 ref，设置 `view="dialogue"`、`locale="CHS"` 和 `limit`（1–20）。
4. 返回 `next_cursor` 时，用相同 anchor、view、limit、locale 续读。不能自行拼接 ref 或 cursor。
5. 需要前文时，用 `follow_evidence(relation_kind="dialogue_predecessor")`，选择返回的前驱 ref 再读。它是对明确来源连接的反向核验，不是数据库相邻行。
6. 对最终使用的 `nodes[].texts[].evidence_ref` 和必要名称/标题 ref 调用 inspect。`node`、ordinal 或路径不是可替代的文本引用。

## 交付内容与边界

- 每个节点分别返回原文、locale、完整文本 SHA、原 observation 的 provenance、可用的文本 proof 及来源连接。
- Dialog 的角色卡沿 `talkRole` 到指定 NPC 物理记录，再读该 locale 的名称字段。`source_role_resolved` 表示来源引用和名称解析唯一，不证明唯一剧情人物或真实发言者身份。同名、同文的多个 occurrence 不合并。
- 非 NPC、缺少 discriminator、缺失名称或多候选返回具体 unresolved/ambiguous 状态；保留原始角色类型，不猜玩家、旁白或匿名角色的姓名。
- Quest/Talk 的来源位置沿冻结的 Quest→Talk 和 MainQuest→Quest candidates 反查，返回链路与可回读的标题候选。其归属仍为 physical candidate；不能推断剧情时间、唯一 owner 或完整任务范围。
- DialogExcel 与 BinOutput/Talk 是不同来源命名空间。没有明确跨源链接时，任务归属保持未解析，不按相同 ID 或台词拼接。
- Quest/Talk 当前投影没有正式的 speaker decoder；扩展不猜说话者。阅读卡目前不解析场景名，`scene_display_name` 为空；这不表示来源完全没有标题字段，Dialog 的 `talkTitleTextMapHash` 尚未在本扩展展示。
- Dialog 展开明确的 `nextDialogs`；Talk 仅沿已投影、同物理容器的 local successors 展开。外部候选和 dangling 保留在边信息，不自动跨容器追踪。
- Dialog/Talk 图读取采用广度优先遍历，保留各边的位置与分支，遇到循环不重复节点。Codex 读取则沿已声明的姓名 occurrence 或同物理 section 分页；这些展示顺序均不是游戏播放顺序或叙事时间顺序。
- MCP 的对白读取默认 `detail="reading"`：正文、来源角色标注、标题及定位信息优先；完整重复 provenance 改由 `inspect_evidence` 展开。Codex 页面用 `source_titles` 与 `title_indexes` 复用同一 exact title；前后节点提供可继续读取的定位信息。需要完整前后文或旧响应形式时显式指定 `detail="full"`。直接调用 Python library 仍默认 full。
- reading 页面最多 160,000 序列化字节，full 保留 80,000 字节上限；最终 MCP envelope 另受 262,144 字节限制。`limit` 是请求主节点数，不是包含翻译、姓名、标题和邻居后的条数。`page_info` 返回请求数、实际主节点数、文本条数、字节限制及停止原因；`requested_node_limit`、`response_byte_limit` 与范围耗尽分别报告。Dialog/Talk 连续图视图最多访问 128 个节点。超大单节点明确拒绝，不偷偷截断原文。
- cursor 必须沿用相同的 anchor、limit、locale 和 detail；不能把 reading cursor 用于 full。按返回的 `next_cursor` 续读，不能用实际交付条数猜下一页。
- `reachable_local_graph_exhausted` 只表示当前可达局部图已遍历；不表示整个任务、其他分支来源或全 corpus 完整。

## 回答与引用

优先说明人物所说内容、回应和冲突，再给已核实的可读来源。角色自述、转述、事实、解释和预测分开；保留否定和“可能”等来源限定。读取局部图时说明覆盖范围，不用一行引用支撑整段未读取的剧情。

内部 ID 与路径留在来源详情。未解析标题不得改写成虚构章节名。来源角色标注与剧情说话者的确定性要分别表述，不重复用笼统免责声明替代已取得的信息。

每个连接默认提供短句柄，如 `[H…:R1:E3]`，以及便于显示的 `citation_number`。reading 对白中的正文、姓名、标题和邻居定位使用 `citation_handle` 字段，不让模型复写长 ref。将完整句柄原样传入 read/follow/inspect 的 `evidence_ref` 参数；inspect 和 full 读取返回原 exact ref 与完整来源。单独的 `R1:E3` 只是显示编号，不是定位器。句柄仅在原连接有效，过期、损坏或其他连接的句柄明确失败，不猜测恢复。重启后应使用保存的 exact ref 重新读取。

有固定快照身份及合格来源路径时返回真实上游文件链接；它定位文件，不承诺网页能直接打开某一 observation 或范围。没有依据时不生成伪链接。来源标题与角色标签是来源观察，不证明唯一人物身份。`display_groups` 的 `representative` 和 `occurrences` 指向本页 node/text 位置，正文只从 nodes 读取，不在分组中重复传递。所有物理 occurrence 保持独立，不能将展示分组理解为合并人物或台词身份。

默认句柄保存在连接内存中；`--citation-receipts <private-directory>` 另外保存私有不可覆盖回执，供现有 capture/project renderer 确定性展开 exact ref。模型仍选择引用并 inspect，程序不改事实、不补引、不替换材料。原生 Codex TUI/Desktop 不自动获得项目 renderer；仅有短句柄不等于最终答案已获得完整引用交付保障。

### 保存可回查的来源卡

`scripts/serve_dialogue.py` 和 `scripts/serve.py` 的五工具 surface 默认将来源卡放在运行配置同目录的 `source-cards/`，仅实际保存时创建。可用 `--source-cards <private-directory>` 指定稳定的本地目录，不复制数据库或修改用户全局配置。直接使用库或其他启动路径时，以 `describe_corpus.source_card_delivery.available` 为准；不可用时显式报错。

`scripts/check_dialogue.py` 也会保存少量检查样本并核对卡片原文/provenance；配置目录只读时，同样传 `--source-cards <writable-private-directory>`。权限不足明确失败，不提升权限或把未保存写成成功。

模型先 read 实际正文，保留该次读取返回的完整 scoped handle，再调用现有 `inspect_evidence`，附加 `save_source=true`。原 `inspect_evidence(corpus_ref, evidence_ref)` 用法保持；保存操作需要 read handle，因为 bare exact ref 不能说明选择的是哪次 observation/range。搜索摘要、未读 locator、inspect 自己生成的 handle 和过期 handle 不能代替已读材料。

成功时 `data.source_card` 返回 `markdown_link`、`markdown_path` 和 `metadata_path`。最终答案将真实卡片链接放在它实际支持的陈述旁；不需要原生客户端自动展开临时 handle，也不拦截或改写模型答案。Markdown 仅展示标题、正文和必要的来源角色标注；路径、哈希、引用 ID、解析状态及限制保留在独立 JSON，不混入面向人的阅读卡。文件级上游链接仍不冒充精确段落链接。

连续对白可一次选择多个实际读过的句柄：

```json
{"corpus_ref":"<已返回的corpus_ref>","evidence_refs":["<第一条已读handle>","<第二条已读handle>"],"save_source":true}
```

`evidence_refs` 与单条 `evidence_ref` 二选一，每批1–32个不同句柄；同一 ref 的不同 observation/range 可分别选中。不需要逐句生成卡片。每项独立 inspect，任一选择失败则不生成集合卡，返回 `partial` 和 `NOT_SAVED`；成功项和失败原因仍分别可见。材料按明确选择顺序展示，不自动跨来源拼成连续发言、不认定人物相同；集合 JSON 的 `entries` 保留每条独立身份、原文、范围和正式检查结果。相同文字来自不同记录时不删除证据。

批量/保存默认返回精简结果，避免再传一遍全部 provenance；`detail="full"` 可显式展开。原单条普通 inspect 默认仍为完整信息。Markdown 使用新阅读视图文件名，不覆盖已交付的旧卡片；机器记录仍保留完整原文与证据链。

JSON 保留原始字符、SHA、原 read mapping 和本次正式 inspect 结果；Markdown 为其阅读视图，缺少末尾换行时只补显示换行。不同 physical occurrence、observation 或范围分别保存；相同内容不合并身份。重复保存同一结果可复用相同字节，已有文件若发生漂移则拒绝覆盖。保存失败不会返回 `SAVED`。

重启 MCP 后卡片仍是普通本地文件；旧 handle 不恢复为新连接的定位器，重新核验使用卡片中的 exact ref。卡片只保存当时已读范围，不获得更多剧情、speaker 或顺序证明，也不自动检查邻近 claim。可以阅读已生成的引用卡；查新材料或重新验证仍通过 MCP。卡片含游戏文本，保留在私有输出目录，不提交 Git 或打入公开代码包；使用临时目录时须自行保留需要长期引用的卡片。已交付引用所依赖的卡片不当作普通可重建缓存删除。

人物阅读面板另有 `groups`：角色材料候选、来源标注的发言候选、正文关键词命中、姓名及未分类结果分别提供指向原 items/previews 的下标，并可按已返回的来源标题组织。它只组织当前页，不改变排名、过滤、cursor 或物理身份；正文关键词命中不证明该人物是说话者。

## 人物搜索与调用方展示

本分支的 literal 采用 `body-first-v1`：精确的已声明标题优先，其后是已声明正文（包括 Readable 行）、其他标题、未分类文本和姓名定位。相同等级内先展示各个同文组的首条，再交付其余物理 occurrence；同文分组按 locale 区分，不合并证据或人物。未知字段不排除。该排序在取页前执行，分页仍包含全部匹配记录，不是只重排第一页。

新 cursor 绑定排序版本及 rank/repeat/id。旧顺序 cursor 明确拒绝，需重新搜索；不可拼接两种顺序。semantic/lexical/hybrid 沿既有发现路径，未增加查询模式、实体识别或新的关系。正文命中附直接读取动作；来源已有 display title 时进入标题分组。没有姓名行的首页仍可有界定位精确人物姓名锚点，供已声明 Character/NPC 导航使用；带过滤的请求不自动扩出范围。

`search_evidence` 默认 `detail="reading"`，先返回 `reading_view`：正文预览、已声明正文字段命中的原始 item 下标、单独姓名与标题命中及可直接执行的下一步。标题不计为正文，未分类字段保留在 `other_matches`；字段未分类不表示原文不存在。标题的读取动作只使用本次命中返回的物理 container/ref。精确姓名可补取最多三个来源角色发言节点；存在 qualified Character adapter 时，以其实际 family 做一次有界 literal 查找，再读至多两个已有 Story/Voice 候选导航目标。它不更改 literal/semantic 排序、原始 items、过滤或 cursor；不是新的身份解析器，也不承诺搜齐人物全部剧情。

带 family、source、历史版本或 domain 范围的查询不自动补取范围外预览；hybrid 自动解析得到的 `domain_filter` 同样受此规则约束。下一步动作仍可显式选择。`detail="full"` 省略预览面板，保持原始检索路径。正文预览必须继续 read/inspect 后才能用于最终引用。材料已足够就回答，证据不足则说明 partial，避免反复改写同义关键词。

MCP SDK 可同时提供兼容文本 `content` 和 `structuredContent`；调用方应优先消费存在的结构化结果，否则解析文本，不能把两份都展示。项目检查脚本已按此处理；其他客户端的重复展示及模型的无效搜索仍需由各自调用方控制。

搜索面板的 `text_groups` 同样只提供 `representative.preview_index` 与 occurrence 下标，正文、独立 ref 和 proof 在对应 `body_previews` 中。完整搜索 envelope（包括引用元数据）在交付前检查字节上限；放不下时从末尾减少可选预览并标记 `omission_reason="response_byte_limit"`，不截断保留的原文、不改变原始命中或 cursor。极接近上限时可以省略整个面板及可选句柄元数据，使用原始 exact refs 继续 read/inspect；不把可选展示超限变成无结果，也不留下未交付的句柄或回执。

首条发言本身超过对白视图预算时，仅省略可选预览，标记 `preview_read_byte_limit`，保留成功搜索及 cursor，并提供已核验正文的普通物理读取入口；不是“没有发言”。只有专门的视图预算异常允许这样处理，来源 SHA、scope 或其他资格错误仍正常拒绝。

紧凑邻居及导航目标标记为 `locator_only`：可以定位并 inspect，但仅 inspect 不表示正文已交付。必须实际读取同一 exact ref，才能满足现有 read-and-inspect 交付条件；优先引用实际正文读取返回的句柄。定位器原记录仍保持未读，不由程序改写成正文或自动更换引用。

默认 reading 进一步省略重复的路径、哈希及 provenance，保留正文、标题、来源角色标注、候选限制和分页状态。普通物理读取中，同一 exact anchor 在 context 内重复出现时只省略其重复正文并标 `body_in_anchor`；其他记录即使文字相同也不删除。完整响应仍由 `detail="full"` 获取。`reading_actions` 给出保留全部分页参数的下一页调用。Codex 的 `nodes[].context_action` 以 full 单节点读取同 section 前后文；Dialog/Talk 则读取当前节点及有界局部后继（请求3节点，仍受字节预算限制），其 `predecessor_action` 返回已核验前驱定位，选择返回目标再读取前文。两种来源的上下文合同不同，均不证明剧情顺序，不需要猜 anchor 或重搜姓名。

搜索和读取发送标准 MCP 进度通知，批量核验还逐项报告已处理数量；只有请求进度且支持展示的客户端才会看到。通知报告实际阶段，未实现正文逐条流式交付、单次 SQL 中途取消或原生 Codex UI 改造，不承诺查询提速。独立查询可由客户端分别展示，不应把 `content` 与 `structuredContent` 重复输出。

验证记录见 [对白阅读验证](../history/dialogue-reading.md)。

人物到任务档案发言的修复、真实 MCP/Sol 检查与保留失败见 [案例验证](../reviews/npc-dialogue-discovery.md)。
