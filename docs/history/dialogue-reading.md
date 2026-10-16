# 对白上下文验证记录

日期：2026-10-12。范围是源码的显式对白阅读路径，不是封存 release 的自动晋级。数据库和统一索引复用已独立重建并核验的 35-input 数据；没有数据库写入、重建或复制。

## 本地验证

- 合成测试：40 项通过，其中原公开源码测试 17 项、新增对白定向测试 23 项。覆盖分支、循环、相同文本不同 occurrence、角色名多候选、缺失 locale、角色/正文/坐标漂移拒绝、分页隔离、前驱、Quest 反向链及不修改原 runtime 的组合 adapter。反向边按声明的 reference kind 和 exact run 过滤。
- 三个运行前固定的来源探针：`向着星辰`/Dialog、`冒险家协会`/Dialog、`摩拉`/Quest-Talk。分别读取局部节点，正文、角色名称及 provenance 经普通 read/inspect 再核对；三项通过。这不是全对白覆盖率。
- 真实 stdio MCP：精确五工具，Dialog 两页不重复；正文和 provenance 回读一致；错误页大小重用 cursor 返回 invalid_request。
- 保留的正式 v1.1 runtime 先执行原始发布资格验证，再组合显式阅读扩展：来源探针及上述 stdio MCP 检查同样通过。未修改正式 receipt、current 或原 runtime；扩展本身尚未作为新正式 release 接受。
- Quest 标题链定向核对：`吞金和蓄财` 的两个来源 Quest root 分别得到一个 MainQuest 位置候选及一个可回读标题。候选链保持物理归属资格，不据此证明唯一剧情身份或完整任务。
- 保留的原重建 MCP smoke：五工具通过；Book 12 页覆盖物理行 0–84 并到当前文件 EOF；Story 400–576 字符续读和范围 inspect 通过。该检查运行未修改的保留实现，不冒称新对白代码重新验收所有功能。

## 保留的初轮失败与修正

1. Talk 合成 fixture 的 INSERT 占位符比 schema 多一列。修正 fixture 后重新运行全部测试；没有放宽断言。
2. 真实 Dialog probe 暴露 ordinal 与 occurrence 身份混用。修正为 exact source/run + physical primary key 定位，再保留原 occurrence ref；合成 fixture 同步使用非 ordinal 的 occurrence，防止掩盖错误。
3. 一个附加诊断假设书名必在 Readable 正文中出现，直接索引空 search 结果而失败。书名可能来自 Document 标题，不能据此判定 Book 不可读；独立 Book continuation smoke 仍通过。没有更改 literal 搜索或给该书特判。
4. review 后补充反向边 run 谓词时，误用 `excel_row.ingest_run_id`，导致两项定向测试失败。按实际 schema 修正为 `run_id`，增加三项 kind/run 隔离测试后全部通过；没有改数据或 schema。
5. 一个额外 Python 诊断先导入公开源码模块，再尝试加载封存 root，因模块缓存和正式 profile 不匹配而拒绝启动。按文档的独立进程加载顺序复验，两条 Quest root 的反向标题链均通过；未放宽正式资格。

## 真实模型记录

冻结自然问题：冒险家协会有关报酬的对白在哪里出现，来源标注是谁说的？请读必要前后文，给出有限范围的解释和引用。

客户端实际为 Codex CLI 0.162.1，请求模型 `gpt-6.1-sol`、reasoning medium、单次 timeout 240 秒。问题中未提供正文、路径、ref 或标准答案。

- 配置初轮：对不存在的 MCP 表只设置 enabled=false，客户端以 invalid transport 在模型启动前退出。原记录保留。
- 可选服务复验：模型完成网页回答，但只调用资源发现，未调用 Research MCP，因此不计数据库答案验收通过。过程耗时约 86.43 秒；网页答案不作为游戏数据库证据。
- 必需服务复验：仅增加 required=true，等待服务资格验证后再开始。模型自主搜索关键词、读取对白图与前驱、inspect 最终材料并形成答案；共 28 次 Research 调用，进程 150.082 秒，记录到的 MCP 累计 3.236 秒。模型读取的本地文件仅为项目说明，没有通过游戏文件或 SQL 绕开 MCP。
- 答案区分三组报酬相关对白，使用来源角色名称“凯瑟琳”，同时说明其不证明唯一逻辑 NPC；任务与场景未解析、局部缺失中文节点及遍历范围明确披露。没有补造任务名、报酬数额或完整剧情。
- 模型选择的 scoped handles 经既有 capture renderer 展开为 DELIVERED，无解析错误；原模型答案与映射分别保留。这是 capture 后交付验证，不代表原生 Codex TUI 已获得确定性 renderer。
- 真实模型会话使用独立重建数据；封存正式数据的扩展另经程序化来源核对与 MCP 检查，尚未重复真实模型会话。不能将这两个验证层混为一次正式发布验收。

完整 native trace 与私有 citation receipts 本地留存，不提交游戏对白或机器路径。此文档仅发布验证范围、失败归因和结论。

## 集中只读 review

GPT-6 Luna Max 独立审查源码、测试、调用记录与真实最终答案，没有发现阻止显式 opt-in 扩展的已证实错误。指出的 Python adapter 默认值差异与反向边缺少显式 kind/run 谓词已集中修正并定向复验。场景/标题字段尚未展示的限制已澄清。review 未重复执行主线程测试，也没有把重建数据上的模型验收写成正式 runtime 模型验收。

## 保留限制

DialogExcel 没有已声明的 Quest/Talk 跨源链接时，其任务归属未解析；Quest/Talk 当前没有正式 speaker decoder。场景地点未声明，不能凭任务名补造。局部图遍历不等于剧情播放、整个任务或全 corpus 完整。部署正式入口及其 citation renderer 没有在本轮被替换。
