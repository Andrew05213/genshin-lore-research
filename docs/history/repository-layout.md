# 仓库目录与许可整理验证

日期：2026-10-16。范围是公开源码的可读命名、目录职责和原创代码许可，不是游戏数据升级或正式 Research 发布。

## 修改范围

- 对外项目及分发包统一为 Genshin Lore Research / `genshin-lore-research`，导入包保持 `genshin_lore`。
- 六个常用命令使用 build、serve、check 等用途名称；底层命令归入 builders、validators、maintenance，更新动态导入、子进程路径、脚本根目录和配方引用。
- 对白实现归入 `genshin_lore.dialogue` 包，未修改读取、关系或引用判断算法。
- 操作指南和历史记录分开；历史 JSON 原字节保留，Markdown 只修正相对链接。
- 原创源码和文档改为 Apache-2.0，添加 NOTICE；旧 Git 提交、归档许可和第三方数据权利不被追溯改写。

## 执行结果

| 检查 | 结果 |
|---|---|
| 全部合成测试及新增布局检查 | 51 项通过 |
| 36 个命令的 `--help` | 全部正常 |
| Python 源码与脚本编译 | 通过 |
| Markdown 本地链接 | 通过 |
| Activity 来源构建 A/B | 两次独立构建、独立 verifier 和对照通过 |
| Activity A/B artifact | 各 94,208 B；SHA-256 均为 `95d9149a8b274cc14d67eeda76aefd973d245df08e280fd14b0dd80abbaea1e1` |
| 重命名后对白 stdio MCP | 精确五工具、两页不重复、正文/provenance 回读、错误 cursor 拒绝均通过 |
| Python wheel 构建 | 成功；Name 为 `genshin-lore-research`、License-Expression 为 `Apache-2.0`，含 LICENSE 与 NOTICE、新 dialogue 包 |
| 本机既有日常入口配置检查 | 通过；未替换其固定工作目录或 MCP 配置 |

研究虚拟环境未安装 pip/setuptools，首次 wheel 命令未执行构建；随后用隔离构建环境成功验证，没有向研究环境安装构建依赖。

## 资格与未执行范围

对白 MCP 检查先由保留正式 runtime 核验其原有数据，再加载新阅读包；没有修改其 receipt 或数据库。完整重建、Book/Story 和模型回答使用既有实验记录，本轮未重新执行 35-input 全量构建或真实模型矩阵。布局测试和小样本构建不能代替全量来源验收。

部分构建身份包含脚本路径或字节。移动这些脚本后，新构建应生成自己的 parser/run 和 artifact seal，不能冒称沿用原字节身份。历史配方中的 source snapshot、表名、合同、SQL migration、coverage 分母和合法 partial 没有按命名审美重写。

维护采用主线程差异审查与定向验证，未另行声称独立模型 review。原始运行输出本地留存，不将游戏原文、数据库、个人配置或大型会话记录提交仓库。
