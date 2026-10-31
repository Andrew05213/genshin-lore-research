# 当前状态与验证范围

## 可执行入口

最新 Linux 固定来源对照对应 `5495c0d`：R0 storage v2 与默认取消索引 VACUUM，35 输入、33 个 core providers、60 个数据库阶段及默认向量完整新目录构建通过。同期 `e231872` 控制组 37:50.51，候选为 35:38.49（各一次），减少 5.81%；观测构建峰值 25.53 → 16.49 GB，完整输出 19.81 → 16.49 GB。全部 R0 字段、正文物理坐标和向量数值对照通过，两次完整索引 oracle 与实际 MCP 续读/引用均通过。30 分钟目标尚未达到；原生 Windows 完整来源计时未运行。取舍、采样空档、资格范围和原始记录摘要见 [构建存储优化](compact-evidence-storage.md)。

上一轮 `36aea75` 的同期 main 控制组 33:54.14、候选 `--jobs 3` 32:27.06（-4.28%）、669 项合成回归及两平台 CI 保持其原验证范围，见 [关键路径优化](build-critical-path.md)。不将不同运行条件的历史数字拼接为此次提速。

上一轮 `2bafeca` 的串行基线 48:01.72 与优化版 33:53.58、652 项合成检查保持其原验证范围，见 [完整冷构建性能](cold-build-throughput.md)。此前分段执行的 [非向量完整构建记录](nonvector-build-performance.md) 不改写为当时已完成单命令向量验收。原始失败和历史全量记录继续保留；不公开模型或向量缓存，没有自动部署或修改正式 current。

进程内独立输入缓存与 v3 阶段回执的原始定向测量见 [构建复用](build-reuse.md)。阶段恢复须核对完整实现/输入/环境/回执身份；本次完整来源成功不是放行旧版或失败回执的许可。

随后针对 PR #13 修复 worker 后代未随失败退出的问题：Linux 独立进程组、Windows Job Object 和启动门，确保停止整棵树后再交付诊断。Linux 完整合成回归 661 项通过，两平台原生路径由当前 CI 检查；这是失败清理补丁，没有重跑或重标上面的完整来源/耗时验收。见 [修复与测试范围](reviews/worker-tree-shutdown.md)。

默认完整构建已包含向量阶段。Linux 在 `cf1788f` 完成了新的 35 输入、33 个 core providers、统一索引完整来源核对及 106,589 个有界向量窗口重建。后续 embedding 优化及独立验证见 [Linux 性能记录](linux-build-performance.md)。原生 Windows 后来在 [PR #9](https://github.com/Andrew05213/genshin-lore-research/pull/9) 的 `5c421f7` 记录完成 35 输入、默认向量与新配置 MCP；不是以 Linux/WSL 结果代替 Windows。[工程整改记录](engineering-finalization.md) 保留当时的执行范围。

此前 `ce53191` 整合了 Linux 线程/组批、Windows 来源绑定/磁盘 staging 和人物对白阅读；当时 556 项合成测试、真实 hnswlib 路径、定向来源 A/B 与有界真实模型比较通过，详见 [整合验证](reviews/linux-windows-integration.md)。该次整合没有重跑完整 35 输入，不继承两个父分支的全量成功资格；后续完整来源执行由上方记录独立证明，没有改写当时结论。最新实现尚未做原生 Windows 完整来源重建，不能用 Windows CI 合成测试代替。

历史完整新目录实验通过 `PASS_COMPLETE_BOUNDED_DATA_REBUILD`：35 个输入、33 个 core contracts、统一索引的完整来源 rowset 核对及 MCP 续读检查通过。此结论不包括 semantic 重建或原部署正式 release 身份复刻。

后续工程整改加强了验证和恢复资格、独立 rowset 检查、输出隔离与 MCP 兼容测试。四次完整重建失败、修复与定向检查保留在 [重试记录](full-rebuild-retry.md)、[合同核对](build-contract-audit.md) 和 [工程质量记录](engineering-quality.md)。第五次 Linux 完整重建在修复后通过，原始失败记录仍保留；2026-10-19 的性能实验只重建和验证受影响的完整 semantic 组件，没有再次重装 35 个数据库输入，不将其与最新完整来源执行混为一次验收。

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

上游源码获取命令列在重建指南中。上述历史实验使用已有固定快照，未重新执行网络下载。后续工程检查另在新目录联网取得固定 commit 的最小来源并完成 Activity A/B；这不等于完整源码从零重建，也不保证上游永久可访问。

## 完整入口与剩余范围

早期整库探针缺少 core 装配、Character 后续文本及统一 Research 的清单/绑定，当时结论为 `PARTIAL_PUBLIC_DATABASE_REBUILD`。其原始实验记录不改写；新的完整链路单独验证并记录结果。

完整链路补齐静态构建 scope、动态来源绑定、全部独立输入、统一文本/关系核对和 MCP 启动。操作见 [完整重建指南](guides/building-dataset.md)。安装源码不会自动配置 Codex；客户端连接须使用本次生成的 runtime 配置。

当前 `--stage all` 包括独立 semantic 构建及完整来源窗口核验，模型必须事先显式取得；`--without-semantic` 保留基础数据模式。默认搜索仍为 literal。向量合成测试和固定模型小型 A/B 不能代替固定范围的真实来源重建，也不证明全游戏覆盖；原部署正式 release 的 acceptance 不由此入口生成。35 个 exact inputs/33 个 core providers 是有界数据范围，不是全游戏覆盖率；不能将来源核验结果写成正式产品自动晋级。

## 研究能力的固有限制

即使运行配置完整，候选归属/说话者/顺序、有限活动正文、版本首发归属和 corpus 完整性仍须按具体证据判断。内容存在于固定快照，不等于官方首次加入版本已证明。完整读取一个文件，也不等于获取全部相关剧情。
