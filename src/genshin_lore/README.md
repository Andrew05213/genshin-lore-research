# 源码导航

外部命令从根目录 `scripts/` 进入；本目录实现来源解析、证据数据和只读检索。项目名称是 Genshin Lore Research，导入路径保持 `genshin_lore`。

| 工作内容 | 从哪里读 |
|---|---|
| 五工具注册与参数 | `research_server.py` |
| 搜索、读取、关系与来源核验 | `research_query.py`、`research_index.py`、`research_refs.py` |
| 统一 7.1 来源适配 | `research71_candidate_adapters.py`、`research71_candidate_relations.py`、`research71_candidate_query.py` |
| 统一任务与完整正文 | `research71_unified.py`、`narrative_assistant_7_1.py`、`research71_preview_server.py` |
| 对白局部图、来源角色和前驱 | `dialogue/context.py` |
| 引用标识传递 | `citation_handoff.py` |
| 显式 lexical/semantic 发现 | `lexical_discovery.py`、`semantic_discovery.py` |
| 各类来源 | `character_*`、`quest_*`、`narrative_*`、`event_*`、`native_*`、`readable_*` 等 |
| Schema20 装配与原 core providers | `schema20_7_1_*`、`m3_7_1_provider_*`、`special_core_7_1_*` |

同一领域通常以 `*_source.py` 处理来源，以 `*_build.py` 生成数据，以 `*_verify.py` 独立核对。历史模块还使用 evidence、maintenance、retrieval 等名称；这些是现有实现，不代表所有接口都是当前对外入口。

优先沿 [架构](../../docs/architecture.md) 的数据流阅读，不按文件名中的阶段号推断接受状态。新领域代码按职责组成小型包，参见 `dialogue/`；不要继续用施工阶段号扩展顶层平铺模块。现有模块分包须单独核对导入、来源构建身份和资格绑定，不能仅移动文件后沿用旧 seal。
