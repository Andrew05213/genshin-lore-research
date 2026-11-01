# Research 阅读展示改进

本改动属于源码运行路径的展示和调用说明维护，不是新数据构建、封存 release 晋级或新的身份证明。五个工具名、literal 默认、搜索原始 items/排序/cursor、exact refs、候选关系与 partial 保持；没有修改数据库、索引、embedding 或来源原文。

## 实际问题与处理

- 人物名检索的原始首屏可能全部为姓名字段。新增 `reading_view`，先给有界正文预览、正文命中位置和后续动作，姓名字段单独列出。精确姓名复用已有 NPC 发言读取及 Character 候选导航；不合并同名对象，不要求检索器识别唯一人物。
- Character 预览只采用现有 decoder 声明的正文角色字段，标题另列；初次检查发现标题混入预览，修复后重新核对。最多一次额外 Character family literal 查找、两个导航目标、每个目标一次有界物理读取；最多展示每目标两条正文。family 来自当前 qualified component，不使用人物白名单。
- MCP 对白默认 reading 视图压缩重复 provenance，标题按 exact ref 在页内复用，邻居保留可回读定位；完整上下文用 `detail="full"`。页上限仍有界，实际主节点数、文本数及停止原因通过 `page_info` 交付。
- reading 正文用连接内短 `citation_handle`，另有可读编号、来源标题/角色标签及真实上游文件链接。inspect/full 恢复 exact ref；链接仅到来源文件，不冒充 observation 网页。原生 Codex renderer 未被修改；完整引用表仍须显式项目 renderer/receipt 路径。
- 同文展示组保留各物理 occurrence 的位置和独立句柄。程序不选证据、不补引、不修复模型的错误事实。
- 错误返回当前连接的 corpus_ref 和参数纠正说明；describe 可省略 ref 来发现此连接身份。调用方优先消费存在的 structuredContent，否则才解析 content，不重复展示两种表示。

详细参数、范围和兼容方式见 [对白阅读指南](../guides/reading-dialogue.md)。Python library 默认仍 full；旧 full cursor 必须显式沿用 full，不与 reading cursor 混用。

## 本次验证

固定来源上的真实 MCP 程序检查，不是模型自主回答验收：

| 查询 | 原始首屏 | 新增正文预览 | 独立 read/inspect 核对 |
|---|---|---|---|
| 沃雅妮莎 | 5 条姓名字段 | 3 条来源角色发言 | 3/3 原文一致 |
| 胡桃 | 5 条姓名字段 | 2 条 storyContext 正文、3 条发言 | 5/5 原文一致 |

同一服务、同一姓名/语言及 `limit=20`：

| 格式 | 首屏主节点 | 实际结构化 envelope UTF-8 字节 | 停止原因 |
|---|---:|---:|---|
| full | 3 | 79,712 | 80,000 字节内部页上限 |
| reading | 17 | 101,757 | 160,000 字节内部页上限 |

这是有界节点交付量改善，不是回答速度测量。reading 三页取得 51 个独立节点，full 十二页取得 36 个独立节点；共同前 36 个节点的 exact ref、正文 SHA 与 proof 完全一致。reading 正文均用 inspect 和普通物理读取回查。两条路径仍有 continuation，不能声称该人物发言已搜齐。

首轮实际来源诊断在 full 读取 30 页时触及诊断脚本上限，保留该记录；没有将它算作服务失败或全范围通过。后续明确采用上述跨页前缀核对；小型合成来源则穷举至末页，另测字节边界、同文不同 occurrence、cursor/detail 错配和合法 partial。

完整合成回归：731 项通过、1 项平台相关跳过。包括实际 stdio 注册与请求、默认紧凑读取及 full 兼容、句柄跨连接拒绝、不可变回执/capture/render 绑定、未 inspect 拒绝交付、正文/标题区分、过滤范围、重复文本身份及调用方单份 payload。编译、六个公开命令 help、文档链接与 Apache-2.0 检查通过。保留正式来源的 `check_dialogue` 两页、原文/provenance 等价及 cursor 错配拒绝通过。

没有运行新的真实模型矩阵，没有重新构建 35 输入，也没有将此分支自动部署到已有客户端或改写其机器配置。原生 Windows 的实际游戏来源读取另需实机验证；两平台合成结果以本 PR CI 为准。

## 保留边界

reading_view 是有界补充面板，原搜索分页没有被重排；family/source/历史版本过滤时不自动跨范围补取。首个有界 Character 姓名候选不是唯一人物，其他候选和后续入口仍可检查。只有名称匹配、没有来源正文时不伪造介绍。

紧凑视图节省重复来源信息，不丢弃 proof/候选限制；完整 provenance 留在 inspect。EOF 仅属于当前读取范围。同文不等于同一说话者，来源角色标签、数组邻接和图边均不证明唯一人物或剧情播放顺序。最终引用是否支持紧邻陈述仍需模型判断和审查。
