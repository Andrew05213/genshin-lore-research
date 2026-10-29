# 构建 worker 后代退出修复

[PR #13 的 P2](https://github.com/Andrew05213/genshin-lore-research/pull/13#discussion_r4411584404) 针对 `4bd624d`：R0 经 `script_verify()` 启动独立来源验证器；父阶段失败时仅终止 worker，验证器仍可存活。修改前的合成复现确认协调器已报错返回，约 1.5 秒后后代仍写出文件。原复现与失败日志保留，未改写此前完整构建记录。

## 修复范围

- [worker 生命周期](../../scripts/build_workers.py) 使用独立 POSIX 进程组或 Windows Job Object，管理 worker 及正常继承的验证器后代。启动门在加入管理范围后才释放构建代码；无法建立 Windows Job 时拒绝启动，不退回仅终止根进程。
- Linux 先 TERM，必要时 KILL，等待全部活跃组成员退出，再回收直接子进程；已退出的 zombie 不视为仍可写入的进程。Windows 在终止前枚举 Job 成员，保留并核实原进程句柄，终止 Job 后等待计数归零及这些句柄实际退出，再关闭句柄；Job handle 不被子进程继承，配置 kill-on-close。仅有计数归零不能替代进程退出等待。
- 即使 worker 已先退出，仍清理其后代。所有 worker 树先停止，再关闭/写入日志；一个清理错误不会跳过另一棵树。失败输出和 SQLite sidecar 保留，清理不完整明确报错。
- 未加依赖、调度框架或新工具；未改变解析、来源范围、验证条件、模型、schema 或证据身份语义。Job API 采用[微软公开接口](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)，不访问 `Popen` 私有句柄。

## 本次执行

Linux 定向测试 **21 项 / 1.28 秒**通过；完整合成回归 **661 项 / 116.39 秒**通过，源码编译通过。测试覆盖：

- 真实 worker → verifier → writer 的三层进程，单/双 worker；
- 父阶段失败、worker 先异常退出、后代忽略 TERM；
- 返回前确认每个后代已经退出且没有晚到的写入；
- 正常完成保留零退出码、重复关闭；
- Windows Job 分配失败时构建正文未执行、启动失败时 Job 释放；
- 一棵树清理报错时另一棵树仍被停止，诊断保留。

原有仅模拟根进程的超时测试由上述真实 TERM-resistant 后代测试替代，未移除验证/数据断言。Windows 原生 Job 路径由同一测试集在两平台 CI 执行；以 PR 对应 head 的检查为合并门槛。CI 覆盖率增加 `scripts.build_workers`；本地未安装 `pytest-cov`，完整测试未带覆盖率参数，未跳过测试。

首次 Windows CI `e50b57c` 为 **660 通过、1 失败**，位于双 worker 先退出后的存活断言；[原始失败](https://github.com/Andrew05213/genshin-lore-research/actions/runs/40885198527)保留。该日志未记录具体存活对象，清理后仅凭 PID 重新打开进程也不能排除 PID 复用，不能据此断言 Job 泄漏。后续测试在触发退出前持有原进程的 Windows handle / Linux pidfd，明确让 worker 等观测就绪后才退出，并记录每个对象的 wait/exit 状态；“全部原进程已退出”和“没有晚写”两项断言均保留，清理测试自身也只针对所持原进程身份。[进程句柄与 PID 的生命周期](https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers)有别，最终以稳定身份的原生复验结果为准。CI 先运行这组快速取消测试，随后仍执行全部合成回归。

稳定句柄复验 `64366f8` 仍失败（20 通过、1 失败），确认问题不能仅归因于 PID 复用；[第二次原始日志](https://github.com/Andrew05213/genshin-lore-research/actions/runs/40888593260)保留。因此生产代码增加原进程的退出等待，测试显式模拟“Job 计数为零但进程尚未完成退出”，超时必须报错，不能报告清理成功。枚举后重新确认 Job 成员资格，避免等待复用 PID 指向的其他进程；句柄在成功、超时路径都释放。完整 Windows 复验仍以最终 head 的 CI 为准，不以 Linux 模拟代替。

增加退出等待后的本地执行：**24 项定向测试、664 项完整合成回归（117.78 秒）通过**，8 项命令/help/本地链接/许可检查及源码编译通过。完整测试保留两平台同样的原进程退出断言，不增加断言前的额外宽限等待。

本修复未重新执行 35 输入数据构建、向量构建或完整耗时 A/B。此前 [33:53.58 的实测](../cold-build-throughput.md) 仍只属于其冻结实现，不改写为本补丁的新计时或新来源验收。新实现继续遵守 v3 资格身份，不重签旧回执。
