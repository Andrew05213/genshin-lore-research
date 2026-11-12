# 调用方的单范围续读示例

`scripts/read_scope.py` 是可选的 stdio MCP 调用方。它读取调用者指定的一个物理范围，逐页检查正文引用，在该范围 EOF 后停止；不会为了用完预算而探索译本或其他来源。服务本身没有因此自动获得去重能力，也不判断研究问题是否已有充分证据。

先按[完整构建指南](building-dataset.md)准备合格的运行配置。在同一 corpus 的正常搜索、导航或检查响应中取得完整 `evidence_ref`，再运行：

```bash
python scripts/read_scope.py --help
python scripts/read_scope.py --config data/example/runtime-config.json --evidence-ref '实际返回的完整物理引用' --output-dir data/private-read-scope --locale CHS --limit 20 --max-calls 20
```

配置必须显式传入。输出目录必须尚不存在，应位于 Git 忽略的私有目录；其中保存原始协议、调用记录、来源卡和摘要，可能含游戏原文。脚本通过正常的 `serve_dialogue.py --config` 启动检查，不安装服务、不写持久客户端配置。导入和 `--help` 不读取配置、不启动服务。带会话标识的 `[H…]` handle 不能传给新进程；不要拼接引用。

`--limit` 为每页 1–32 条；`--max-calls` 包括 `describe_corpus`、正文和 `inspect_evidence` 等业务调用，不包括协议握手。预算耗尽、读取失败或检查未完成时返回非零退出码和 `complete: false`。来源检查会使用 `save_source=true` 保存私有来源卡；读取 EOF 和来源检查分别记账，检查失败后重试不会把未核验的 EOF 当作成功。

嵌入示例时，`StdioReader.read()` 会为每个已接受的读取页登记待检查正文；随后调用 `read_one_scope()` 会先补齐这些检查再报告完成。范围已读完也不会吞掉陌生 cursor，未知位置仍须交由服务校验。

`scripts/read_scope_state.py` 是独立的连接内状态模块。嵌入调用方时，每个 MCP 连接创建新的 `ReadState`；先用 `prepare(action)` 取得需要执行的原始动作或续页，实际成功收到正文后才调用 `observe(action, response)`。`None` 表示调用方已覆盖相同范围，不是伪造的服务响应。搜索与检查响应只能经 `learn` 建立实际返回的 handle/exact-ref 对应，不能记为正文覆盖。

范围身份包括 exact ref、corpus 和所有读取参数（包括 view、locale、detail、before/after、limit）；只有 cursor 不属于范围键。部分范围保留原 anchor、参数和服务返回的 cursor。只有从首页连续读到明确 EOF 的相同范围才可跳过；未知 cursor、未知短引用、不同上下文窗口和同文不同物理来源均不合并。失败、超时、响应过大或没有可验证续页的 partial 不会完成范围。超时会废弃连接内状态，须新建连接后用完整 exact ref 重试。

该示例以物理范围为 CLI 合同。状态模块能保留对白续页，但旧对白响应的 `partial` 混合了遍历边界与来源不确定性，因此遇到这种不确定链时，即使末页没有 cursor，也保守地不标记整体完成。不把局部图 EOF 写成完整剧情覆盖。

针对性验证使用合成来源和真实 stdio fixture，无需游戏数据库：

```bash
PYTHONPATH=src python -m pytest -q tests/test_read_scope_state.py tests/test_read_scope_stdio.py
```

此工具不依赖 PR24 的排序规则，也不更改检索实现、数据、索引、运行资格或证据身份。更多语言行和更多物理引用不自动代表更高研究质量。
