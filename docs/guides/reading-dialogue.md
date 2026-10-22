# 对白上下文读取

## 范围与启动

对白阅读扩展复用已重建的有界 7.1 数据与统一索引，不生成新数据库。它是显式启用的源码运行路径，不修改已部署的封存 release、current 或客户端全局配置。

完整重建后，使用生成的配置启动 stdio MCP：

```sh
.venv/bin/python scripts/serve_dialogue.py --config data/rebuilt/runtime-config.json
```

工具仍为 `describe_corpus`、`search_evidence`、`read_evidence`、`follow_evidence`、`inspect_evidence`。连接参数指向该脚本与实际生成的配置；不得用文件名推测其他数据实例。启动仍核验 bundle、profile、输入 artifact、索引与来源绑定，`prepublication` 状态不表示正式发布接受。

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

项目日常启动器可复用 `scripts/dialogue_daily.py` 的命令 handoff。在本地 `research-daily.json` 设置绝对路径 `dialogue_runtime_root` 后，调用 `settings(project, config_path, overrides)`，或以 `capture_wrapper` 包装已有 capture。它只替换五工具服务器的启动命令，保留原问题、resume 参数、引用回执目录和原 renderer，不替换 task 便利服务器。`dialogue_runtime.py` 同时接受既有 `GENSHIN_CITATION_HANDOFF_DIR` 环境配置。配置与备份留在部署环境，不提交个人路径。

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
- 读取视图采用广度优先遍历，保留各边的位置与分支，遇到循环不重复节点。该展示顺序不是游戏播放顺序或叙事时间顺序。
- 每页限制 80,000 序列化字节，连续视图最多访问 128 个节点。达到限制时明确 partial；仍可选择已返回的分支 ref 单独读取。超大单节点明确拒绝，不偷偷截断原文。
- `reachable_local_graph_exhausted` 只表示当前可达局部图已遍历；不表示整个任务、其他分支来源或全 corpus 完整。

## 回答与引用

优先说明人物所说内容、回应和冲突，再给已核实的可读来源。角色自述、转述、事实、解释和预测分开；保留否定和“可能”等来源限定。读取局部图时说明覆盖范围，不用一行引用支撑整段未读取的剧情。

内部 ID 与路径留在来源详情。未解析标题不得改写成虚构章节名。来源角色标注与剧情说话者的确定性要分别表述，不重复用笼统免责声明替代已取得的信息。

`--citation-receipts <private-directory>` 可复用现有 scoped handle transport。模型仍选择引用并 inspect，程序只做 exact ref 映射；原生 Codex TUI/Desktop 不自动获得项目 renderer。未选择或未检查的引用不能由程序补齐。

验证记录见 [对白阅读验证](../history/dialogue-reading.md)。
