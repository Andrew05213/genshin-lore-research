# 当前状态与验证范围

## 可执行入口

完整新目录实验已通过 `PASS_COMPLETE_BOUNDED_DATA_REBUILD`：35 个输入、33 个 core contracts、统一索引的完整来源 rowset 核对及 MCP 续读检查通过。此结论不包括 semantic 重建或原部署正式 release 身份复刻。

`scripts/build.py` 提供 35 个有界数据输入、33 个 core providers、统一 Research 索引及生成配置的完整入口。来源绑定由本次构建产生，原来源 oracle 与证据边界保留，不依赖已部署游戏数据库。实际完整验证结果见 [完整重建实验](history/full-rebuild.md)。

`scripts/build_component.py` 仍注册原 14 类基础组件，适合小型来源验证。它不是完整数据入口。

参数与组件标识通过以下命令查询：

```sh
.venv/bin/python scripts/build_component.py --help
.venv/bin/python scripts/build_component.py --list
.venv/bin/python scripts/build.py --help
```

## 原基础实验（历史）

- Linux、Python 3.12；直接依赖版本见 `requirements-build.txt`。
- 固定源码的 commit/tree 和工作区清洁状态核对。
- `activity_archive` 两次独立来源构建和两次独立验证均通过。
- 两次数据库 size/SHA 和构建报告一致；数据库为 94,208 字节。
- 对全部 14 类注册组件执行了真实来源构建。前 11 类命令及核验通过；NPC/Dialog/Reminder 三类暴露了入口适配错误，经修复和定向重建后通过。
- 14 类组件的独立数据库合计 1,372,856,320 字节。新建 A/B 数据库字节均一致；三类叙事组件的完整构建报告含耗时、inode 和容量实测，不声明其报告字节一致。
- 当时新虚拟环境中的直接依赖安装和可编辑安装通过；历史包元数据为 `AGPL-3.0-only`。当前源码许可已按维护者决定改为 `Apache-2.0`，历史实验记录保留原值。

初始最小构建记录见 [构建验证记录](history/component-build-validation.json)，完整组件实验、失败与复验见 [重建实验](history/component-rebuild.md) 及其 [机器可读记录](history/component-rebuild.json)。记录不包含数据库或游戏正文。

上游源码获取命令列在重建指南中。已有固定快照用于上述验证，未重新执行网络下载；上游 commit 的持续可访问性不由本项目保证。

## 完整入口与剩余范围

早期整库探针缺少 core 装配、Character 后续文本及统一 Research 的清单/绑定，当时结论为 `PARTIAL_PUBLIC_DATABASE_REBUILD`。其原始实验记录不改写；新的完整链路单独验证并记录结果。

完整链路补齐静态构建 scope、动态来源绑定、全部独立输入、统一文本/关系核对和 MCP 启动。操作见 [完整重建指南](guides/building-dataset.md)。安装源码不会自动配置 Codex；客户端连接须使用本次生成的 runtime 配置。

semantic overlay、外部模型权重和原部署正式 release 的 acceptance 不由基础数据入口自动生成。35 个 exact inputs/33 个 core providers 是有界数据范围，不是全游戏覆盖率；不能将来源核验结果写成正式产品自动晋级。

## 研究能力的固有限制

即使运行配置完整，候选归属/说话者/顺序、有限活动正文、版本首发归属和 corpus 完整性仍须按具体证据判断。内容存在于固定快照，不等于官方首次加入版本已证明。完整读取一个文件，也不等于获取全部相关剧情。
