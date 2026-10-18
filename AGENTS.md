# Agent 开发规则

先阅读 [README](README.md)、[当前状态](docs/status.md) 和 [开发指南](docs/development.md)。本仓库是源码发行版，不包含已部署的数据、current 指针或发布验收凭据；不得仅凭模块名称推断功能可直接运行。

命名与目录遵循 [规范](docs/naming.md)：项目名称 Genshin Lore Research，源码/文档许可 Apache-2.0；常用命令放 scripts 根目录，各类构建、验证和维护命令分别归类。保留证据合同、迁移和历史记录身份，不按美观改写旧 seal。

## 构建与维护

- 小型组件验证使用 `scripts/build_component.py`；完整有界数据使用 `scripts/build.py --stage all`，运行参数见 [完整指南](docs/guides/building-dataset.md)。实际成功范围以实验记录为准，不凭入口名称推断。
- 输入绑定固定上游快照，保留解析器、导入批次、来源坐标、语言和观察范围；不通过修改旧数据库生成新版本。
- 数据库、上游文本、会话记录、个人配置、凭据和向量/模型文件不提交 Git。单元测试使用合成文本。
- 修改解析器或 SQL 后重新构建和核验受影响组件，不沿用旧字节的校验结果。
- 构建动态绑定只写本次输出的 `metadata/`；源码保留静态合同与 recipe。恢复要求完整 v2 阶段回执及实现/输入/环境/报告 seal 匹配，不自动接受 NOT_RUN、跳过、未知状态或旧回执。
- PR/main CI 运行无游戏数据测试、真实合成 MCP 协议、编译、六命令 help、文档/许可和打包检查；完整来源构建单列，不用历史成功冒充当前代码的全量验证。见 [工程质量记录](docs/engineering-quality.md)。
- 修改构建接线先运行合成全链路测试（含实际索引、独立 oracle 和 MCP），再做来源定向核验；完整重建是最终门槛，不代替状态、参数和动态路径的低成本测试。表范围复用静态合同，未知 scope 拒绝，不按命名自动创造 adapter；合法空表仍须声明。
- 保留已有改动与失败记录；不擅自删除数据、改写 Git 历史或扩大任务范围。
- 公共文档面向仓库读者：命令应可定位到实际脚本，标明验证范围，不混入会话指令、施工进度或个人路径。

## 研究证据

- `Raw Evidence Identity != Logical Identity`：相同 ID、名字或文本不自动合并身份。
- 保留 candidate、ambiguous、unresolved 和 partial；物理顺序不等于剧情时间顺序，EOF 只属于当前文件或观察。
- Semantic 仅用于候选发现，分数不是置信度，也不证明关系。
- 原文引用支持紧邻事实；保留来源限定语，解释和预测与事实分开。
- 已配置研究服务时，研究 Agent 通过 MCP 读取并检查引用。构建器与独立验证器可以直接读取上游源码。
- 允许互联网研究、文学解释与预测，分别标明来源、依据和假设。
- 对白研究先取得必要前后文和来源角色标注，再解释内容；使用声明的 `read_evidence(view="dialogue")` 与续读 cursor，前驱由 `follow_evidence(relation_kind="dialogue_predecessor")` 取得。来源引用解析与唯一剧情人物身份分别判断，不能把遍历顺序写成播放顺序。具体支持范围见 [对白阅读](docs/guides/reading-dialogue.md)。
