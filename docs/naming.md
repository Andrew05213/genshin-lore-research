# 目录与命名规范

项目名称为 **Genshin Lore Research**，公开仓库和 Python 分发包名为 `genshin-lore-research`。Python 导入包继续使用 `genshin_lore`，避免把品牌调整变成无关的全库导入迁移。

## 常用入口

| 用途 | 命令 |
|---|---|
| 完整有界数据构建 | `python scripts/build.py --help` |
| 单组件构建 | `python scripts/build_component.py --help` |
| 启动 MCP | `python scripts/serve.py --help` |
| 检查 MCP 与续读 | `python scripts/check.py --help` |
| 启动对白阅读扩展 | `python scripts/serve_dialogue.py --help` |
| 检查对白阅读扩展 | `python scripts/check_dialogue.py --help` |

所有命令均在仓库根目录、已安装依赖的环境中执行。根目录脚本是常用入口；`scripts/builders/` 是各来源构建器，`scripts/validators/` 是独立校验器，`scripts/maintenance/` 是范围生成和字段证明等辅助命令。不要只为单个组件再增加一个根目录入口。

## 名称规则

- Python 文件、模块、函数使用 `snake_case`；类使用 `PascalCase`，常量使用 `UPPER_SNAKE_CASE`。
- 文件按领域和职责命名，例如 `builders/quest_narrative.py`；不要用新的 phase、spike、M3、final2 等施工标签命名公开入口。
- 文档使用小写名称，多个词用连字符；操作指南放 `docs/guides/`，实际实验记录放 `docs/history/`。
- 代码版本、游戏快照版本、SQL schema 版本、证据合同版本分别表达。游戏更新不要求修改 Schema 编号，代码版本也不是游戏版本。
- 版本号仅在确实区分不兼容解析/合同语义时保留，例如 `quest_review_binding_v2.py`；不要为删除名字中的版本而合并不同来源规则。
- 身份字段、JSON key、表名、迁移编号、parser/run ID、evidence ref 不按文案审美改名。其修改需要独立的数据兼容与来源核验。

## 旧命令迁移

| 旧入口 | 新入口 |
|---|---|
| `scripts/rebuild_complete.py` | `scripts/build.py` |
| `scripts/rebuild.py` | `scripts/build_component.py` |
| `scripts/rebuilt_runtime.py` | `scripts/serve.py` |
| `scripts/check_rebuilt_mcp.py` | `scripts/check.py` |
| `scripts/dialogue_runtime.py` | `scripts/serve_dialogue.py` |
| `scripts/check_dialogue_mcp.py` | `scripts/check_dialogue.py` |

这些源码命令是明确的路径迁移，不提供另一套重复入口。部署更新到新代码时，应同步修改客户端的脚本路径；不得直接移动正在使用的固定 release worktree。已有固定部署仍使用其自身的代码和配置，不受此源码整理自动影响。

## 保留的历史名称

`src/genshin_lore/` 中仍有早期 phase/batch 模块，SQL、来源 scope 和重建配方也保留版本化名称。它们有真实导入依赖和部分来源身份绑定，本轮不批量重写。开发定位见 [源码导航](../src/genshin_lore/README.md)。

历史机器记录保留原字节；历史 Markdown 仅调整移动后的相对链接，不把其中原命令、旧许可或当时结论改写成当前状态。`docs/history/initial-source-files.json`（原名 `PUBLIC_SOURCE_FILES.json`）仍是初始归档清单，不是当前 Git 文件清单。
