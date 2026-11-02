---
name: genshin-lore-research
description: Research Genshin Impact lore with the configured evidence MCP and web sources. Use for character, dialogue, quest, event and book investigations, close reading, foreshadowing and predictions; not for database building or code maintenance.
---

# 原神剧情研究

通过已配置的 Research MCP 取得游戏原文，用互联网补充官方公告、背景资料和社区解释。先确认当前连接的语料和实际能力；源码 checkout 本身不包含数据库，也不自动获得正式发布资格。连接不可用时说明缺项，不通过本地游戏文件或 SQL 绕开研究接口，不擅自启动完整构建。

## 定位材料

- 首次需要确认语料或参数时用 `describe_corpus`；支持时省略 `corpus_ref` 发现当前值，此后复用实际返回值。不要猜 corpus、family、路径、人物 ID 或 ref。
- 模型先理解问题，拆出人物、作品、任务及主题关键词。`search_evidence.query` 传短关键词，不传整句问题。多主体分别搜；引号里的标题不能吞掉句子中另一位人物的名字。
- literal 默认匹配连续子串，不是关键词 AND。姓名和主题线索可分别查询，再用已声明过滤或导航缩小范围；不要拼成原文中不存在的长短语。模糊线索可显式使用 lexical/semantic/hybrid；相似度只用于发现候选，不证明身份或关系。
- 人物搜索先看 `reading_view` 的正文预览、`groups.by_material_type`、正文命中和 `next_actions`。姓名字段只用于定位，不能据此回答人物经历。跨角色资料、Story/Voice、任务档案和对白寻找线索，不只查人物介绍；预览仍需实际 read/inspect 后引用。
- 中文研究优先读 CHS 原文并核对专名，不把自行音译写成官方中文名。别名或同名对象分别保留来源，不能自动合并。

## 阅读上下文

- 通过 `read_evidence` 读取材料，沿工具返回的关系和定位信息继续；不按编号接近、文本相似或同 ID 猜人物归属、任务关联或剧情顺序。
- 人物/NPC 姓名命中可用 `read_evidence(view="dialogue", locale="CHS")` 查来源标注的发言候选；正文再用同一 view 进入所在物理片段。区分“提到了此人”和“来源将此人标为说话者”。同节点姓名标注仍不证明唯一逻辑人物。
- 对白默认 `detail="reading"` 提供正文、标题、说话者标注及邻居定位。需要邻居正文时实际读取其 locator，或显式用 `detail="full"` 取得完整视图；只看 locator 或 inspect 不等于读过正文。
- 用返回的 `next_cursor` 续页，保持相同 anchor/ref、limit、locale、detail。`limit` 请求的是主节点，按 `page_info` 判断实际交付量和停止原因，不自行计算下一页。
- 若返回 `reading_actions`，直接使用其中的下一页参数。Codex 的 `nodes[].context_action` 读取同section前后文；Dialog/Talk 的该动作读取当前及有界后继，`predecessor_action` 查前驱后再选目标读前文。无需再次搜索同一个名字。进度通知是否可见取决于客户端；通知不表示正文已返回。
- `dialogue_predecessor` 仅适用于声明支持的 Dialog/Talk 来源，不用于 NPC 姓名或 Codex 正文。Codex 依其来源 section 和续页入口读取；图遍历、分支连接或物理排列均不证明剧情播放顺序。
- Book/Story 长正文沿当前接口返回的 cursor、continuation 或范围定位按需读取；不同接口参数不可混用。分别记录读过的篇章、物理行/字符范围及是否到达当前文件/记录 EOF，不能由一篇读完推断全部故事或整库完整。

详细参数和来源边界见 [对白阅读指南](../../../docs/guides/reading-dialogue.md)，仅在需要时阅读。

## 深度与研究效率

按用户要求的深度阅读。剧情调查应取得能解释人物行动、冲突、回应和重要线索的上下文，少量样本不是深度研究终点；发现涉及身世、创造者、亲缘或关键事件的线索时，沿其专名和来源继续核实，不凭熟悉的背景知识补全。

普通问题以约 20 次调用为软目标；长文或复杂调查可继续，但应有具体未解决问题。复用已读材料和已检查引用，避免同义 query 循环、反复 inspect 或重复消费 `content` 与 `structuredContent`。证据足够就回答；缺少材料时给有限结论，不无限追求更完美的引用。

长调查保留简短研究笔记：已核实专名、关键事实、对应 handle/exact ref、已读范围和待查问题。按需重新读取，避免把整份重复工具 payload 放进总结。

## 回答与引用

- 正文先解释发生了什么、人物说了什么及其意义，使用已核实的任务/书籍/片段标题；Talk 编号、路径、合同和原始坐标放来源详情。标题未解析时准确说明，不虚构一个好听的剧情名称。
- 最终引用使用实际读过并 inspect 的正文材料。将工具返回的完整 `citation_handle` 原样交给 `inspect_evidence.evidence_ref`；不能自行简写成 `E1` 或拼接长 ref。姓名、标题或 container 证据不能代替正文支持剧情事实。
- `describe_corpus` 默认提供简明发现信息，只有需要具体覆盖表时才取 `detail="full"`。若它声明 `source_card_delivery.available=true`，将最终选中的已读正文 handles 作为 `inspect_evidence(evidence_refs=[...], save_source=true)` 一次核验并保存为一张阅读卡（每批最多32条）。单条仍可用 `evidence_ref`。正文和必要的来源角色标注面向读者；exact ref、范围和检查状态保存在独立 JSON，不抄入回答。使用返回的真实 `source_card.markdown_link`，核对每项状态，失败不冒充已保存。批量只是所选材料的集合，不证明连续剧情；程序不验证 claim，不替换选材。普通 inspect 保留完整来源；批量/保存默认精简，需技术详情时指定 `detail="full"`。
- handle 只在原 MCP 连接有效；过期或跨连接时使用已保存的 exact ref 重新读取，不猜测修复。原生 Codex 不自动展开最终答案中的 handle；只有配置了项目 renderer 的路径才保证 exact ref 展开。需要可长期回查的来源时保留 inspect 返回的 exact ref；可读标题/编号不能替代该身份。
- 每条引用支持紧邻陈述，长总结需要相应范围和多条材料。来源只支持局部时缩窄表述，说明实际读到的部分；不要用一句对白证明整段因果、身份或结局。
- 保留原文的否定、“可能”“据说”、角色自述和转述限定。区分来源事实、人物主张、文学解释和预测。candidate、ambiguous、unresolved 和 partial 不因回答流畅而消失；EOF 不是全角色发言或 corpus 完整。
- 网页独立链接引用：官方公告优先用于版本归属，社区分析标明作者/来源。网页解释不能替代游戏原文或 speaker/owner/order 证明；搜索摘要、视频链接不等于已看过正文。快照存在不证明官方首次实装，未找到不证明游戏中不存在。

## 解释与预测

允许跨材料分析象征、伏笔及未来命运。给出具体线索、推理链、关键假设，以及有意义的反证或替代解释；不把缺少未来事实当成禁止预测。引用支持线索，不证明预测必然发生。若只有一种合理但未证实的解读，明确称为解释，不写成来源已经确认的剧情。
