# 7.2 迁移的原生 Windows 复验

2026-11-05，在原生 Windows 的中文和空格路径中，对 PR #25 的
`e62e69fdbc9663df6155bbd2324edcbf4ad71ff0` 完成新的单命令冷构建及运行时复验。
**现有有界范围的 35 个输入、33 个 core providers、统一索引和默认向量阶段通过。**
这不声明全游戏覆盖、正式 release 晋级，也没有切换既有 7.1 客户端默认。
[此前 Linux 记录](migration-7.2.md) 的失败、恢复、seals 和当时的 NOT_RUN 继续保留。

## 修复与实际版本

初始 PR head 为 `e78b4001686d61e6a76491d79957632d5fe419b1`。
两项独立 review 意见分别在以下提交修复，未修改 parser、SQL、来源正文或旧数据：

- `0d8f3ea554c5b6bdecbb9a7c8117939457561402`：导航对象有两条通过
  `__new__` 创建的实际构造路径；从已核验 query 的 snapshot 初始化版本和来源身份。
  新增两条构造路径及未知 snapshot 的合成回归。
- `e62e69fdbc9663df6155bbd2324edcbf4ad71ff0`：Document 不同物理关系可以共享
  坐标。follow 使用已按主键重检的精确候选行导出 Readable metadata，拒绝错关系或错指针，
  不再通过宽坐标查询重新发现候选；同时补齐 published query 的版本初始化。
  7.1/7.2、重复坐标、无 metadata、错误绑定及禁止展开的真实存储查询回归通过。

另有两处 Windows 测试仅在创建 symlink 返回 WinError 1314 时跳过该权限分支；
hardlink 断言仍实际执行，其他异常继续失败，生产校验未放宽。
没有修改全局 Git 身份、安全设置或执行策略。

冷构建、四命令检查和两次模型会话的实际源码均为 **`e62e69f`**。
之后的文档补充不改写构建 SHA、资格回执或 artifact seals。
该提交的 [Engineering CI](https://github.com/Andrew05213/genshin-lore-research/actions/runs/41956397990)
和 [发行包 CI](https://github.com/Andrew05213/genshin-lore-research/actions/runs/41956397878) 通过；
[本轮自动 review](https://github.com/Andrew05213/genshin-lore-research/pull/25#issuecomment-6636571028)
未发现新问题，两项旧 review thread 已解决。

## 新来源与完整冷构建

环境为 Windows 11 Pro 10.0.26300、Python 3.12.10、SQLite 3.49.1。
使用已有独立虚拟环境及锁定依赖，未将其已有状态冒充缺依赖安装验证。

| 输入 | 固定身份与本次核验 |
| --- | --- |
| 7.2 来源 commit | `d54f6f0d423ded9d055bb740a3e5f3aa8f2667f1` |
| 7.2 来源 tree | `5b334e711319726c504da95dbfe6ebb26baaa7d3` |
| 新 Git checkout | 156,131 文件 / 4,151,856,370 B，全部逐项对照 Git blob，干净且未改写换行 |
| 历史 oracle commit | `26df1dfbdf05a82bbb1d97506859f3e1c40718d8` |
| 历史 oracle tree | `d891ef1d95fdd6c27a1da8a32ea7a61b483647a6` |
| 历史 checkout | 152,886 文件 / 9,154,197,562 B，全部 blob 核对，干净且未改写换行 |
| MiniLM revision | `faf4aa4225822f3bc6376869cb1164e8e3feedd0`，已有权重经当前 checker 核验 |
| 模型目录 | 8 文件 / 266,904,093 B；model tree SHA-256 `a7afbe9e63e0060cf7ca4a995e11ee5c916ff0c129b3096b6309eb530427c19f` |

使用此前不存在的新输出目录，实际运行 `scripts/build.py --source-version 7.2 --stage all`，
显式提供固定 source、historical-source 和 model-cache。
参数为 `--jobs 1 --textmap-cache-mib 256 --embedding-threads 2`，没有使用
`--without-semantic`。没有复制旧数据库、索引或向量，模型推理实际执行。

完整命令 **exit 0，4,720.141 秒（78:40.14）**。
60 个数据库/索引 v3 回执与 1 个 semantic 回执均通过另行审计：
实现/静态合同、固定输入、环境、依赖、报告和实际文件 seals 全部匹配。
33 个 core providers 为 20 个普通合同及 13 个 special providers，未将后者漏计。
没有失败回执、未知状态或遗留 SQLite sidecar 获得成功资格。
`runtime-config.json` 和动态 metadata 均绑定新输出，没有借用旧部署。

两次独立的完整索引 oracle 分别核对 staging 和最终只读映像，均为
`PASS_COMPLETE_SOURCE_ROWSETS`，不是抽样：

| 对象 | 实际数量 |
| --- | ---: |
| Research 输入 | 35 |
| physical containers | 927,621 |
| text occurrences | 3,735,403 |
| relations | 660,120 |
| candidates | 434,558 |
| semantic physical occurrences | 103,188 |
| semantic windows / 完整映射 | 104,558 |
| 实际去重推理字符串 | 69,384 |

全部来源窗口、范围、ref、lexicon 和存储向量绑定的独立验证 PASS；
ANN 与原向量最大绝对差为 `1.1920928955078125e-07`。
默认搜索仍为 literal，semantic/hybrid 仅显式发现候选。

| 本次 artifact | 字节 | SHA-256 |
| --- | ---: | --- |
| Research index | 6,649,327,616 | `0b174e2dff0648ef325e768ade9463a6eddca2d828c82b20aeaf738dd1f4a1e4` |
| semantic metadata | 216,526,848 | `6878a97a892f683b82402b220c98d83079f67bdef9a8adc53d878bf26982d0cb` |
| HNSW | 172,804,484 | `00d8c99cc22bdeb751b2ae2c38939e4ec5defc05f411de1b3e5028f4bc0c82ec` |
| vectors | 160,601,216 | `b43f1f6a987628d305be9e335c7b44367c336cfcba58d6a21b63b4bff8d07739` |
| lexicon | 395,935,744 | `b3fde43b7fbfaaded91f584a104815cc26827d016c496964e1d2fa9aad20d79d` |

这些是 Windows 本次字节，未沿用 Linux seal，也未声明跨环境字节相等。

## MCP、原生会话与引用

以下四条公开入口均使用新生成的 runtime 配置，exit 0：

```text
scripts/serve.py --config OUTPUT_ROOT/runtime-config.json --check
scripts/check.py --config OUTPUT_ROOT/runtime-config.json
scripts/serve_dialogue.py --config OUTPUT_ROOT/runtime-config.json --check
scripts/check_dialogue.py --config OUTPUT_ROOT/runtime-config.json
```

程序化实际 stdio 检查涵盖五工具及独立的两工具便利入口、Book/Story 续读与 range inspect、
对白分页、错误 cursor 参数拒绝、正文/provenance 对照和来源卡回读。
额外协议复验验证 literal/lexical/semantic/hybrid、人物正文预览和分组、
批量卡片的逐项状态、错误/重复/过期 handle 拒绝及重复操作。
新进程中旧短 handle 被拒绝，保存的 exact ref 可以回读，卡片未被改写。

本机 Codex CLI **0.164.0** 使用既有登录和模型设置新建两次真实会话，
每次仅临时覆盖此 MCP 的启动参数，实际读取候选仓库的研究 Skill。
模型仅收到自然问题和通用研究要求；没有预选游戏正文、ref、来源路径或标准答案，
游戏材料全部经 MCP 获取。两次均调用了
`describe_corpus / search_evidence / read_evidence / follow_evidence / inspect_evidence`。

- 对白会话：9 次调用，真实 cursor 两页，另外读取局部前后文并检查来源角色/标题字段；
  回答保留 physical candidate、跨记录身份未定和非剧情顺序限制。
- 故事及模糊发现会话：18 次调用，读完一条未截断的角色故事，未冒称全部故事；
  显式 semantic/hybrid 发现候选，回读命中及前驱上下文后才解释，保留未知说话者和任务候选。
- 两张实际返回的卡片链接出现在最终答案中；共六条选定正文经第三个新 MCP 进程
  再次 read/inspect，正文、范围 SHA 和 provenance 一致，卡片在 ignored 私有目录。

人工核对支持这两次有限观察的正文总结与范围限定。
**对白卡片只保存两条选定发言**；上下文虽另行 read/inspect，未全部收入该卡。
不能声称打开卡片便能独立证明最终答案的每一项上下文描述，也不将链接存在当作 claim 证明。
原生 Codex 没有新增专用 citation renderer，未执行 Desktop GUI 重启验证。
两次 CLI 新会话和另一次 stdio 重启成功，不冒充持久配置已经切换。

既有其他服务器和个人配置的前后 SHA 相同。
客户端同时报告了既有其他服务器路径、无效 Skill/插件的警告；它们未阻断此研究服务，
本次未改动这些设置。

## 原始失败及复验

| 本机记录 | 结果和处理 |
| --- | --- |
| 导航问题的修复前定向回归 | 3 failed；修复后 3 passed |
| 第一轮全量合成回归 | 2 failed / 822 passed / 9 skipped，两个失败为 WinError 1314；精确限制权限跳过后 822 passed / 11 skipped |
| Document 重复坐标修复前 | 5 failed / 8 passed；修复后相关真实索引/oracle/MCP 定向检查 44 passed |
| published namespace 修复前 | 1 failed；补齐初始化后构造及 Document 定向检查 16 passed |
| 诊断计时器回归运行 | 165.921 秒，进程访问异常 `0xC0000005`，保留全部失败产物；根因未确定，不宣称已修复 |
| 最终标准全量回归 | 同一源码，不加可选 traceback 计时器，**826 passed / 11 skipped**，491.531 秒，exit 0；没有排除测试或削弱断言 |
| 本机新的完整来源构建 | 一次新输出冷构建 exit 0；不是旧失败目录恢复或历史 Linux 结果 |

固定 fastembed 0.7.4 在构建及 semantic 查询时报告 mean pooling 行为警告；
依赖未变更以消除警告，构建和运行使用相同固定后端。
初轮失败、访问异常、原始日志和回执均保留，未删除回执或重签旧数据。

## 容量与采样限制

hard **90,000,000,000 B**、warning **77,309,411,328 B** 均未提高。
在明确逐项授权后，仅清理旧输出清单中的 64 个数据库/索引/向量文件，
共 19,776,644,204 B；另外 300 个日志、报告和 metadata 文件保留。
清理前逐项核对路径、大小、SHA、重解析点和使用情况，未删除其他数据。

准备完依赖、来源、模型后的 policy inventory 为 **63,817,682,968 B**，
项目预算余量 **26,182,317,032 B**，主机卷另行满足 23 GB 空闲要求。
Windows 不提供 `st_blocks`，该 policy 字段回退到逻辑大小，不能冒称真实物理分配。
实际卷簇大小的保守文件内容分配上界为 **67,388,535,253 B**，
保守余量仍为 **22,611,464,747 B**；加 22 GB 构建预算及 25 MB 簇余量后，
上界为 **89,413,535,253 B**。此边界不含 NTFS metadata/ADS，不是精确分配测量。

15 秒采样的构建进程树工作集峰值 **3,940,487,168 B**，private bytes 峰值
**2,663,768,064 B**，最低系统提交余量 **4,120,023,040 B**。
输出采样峰值 **16,397,754,493 B**；随后包含来源卡的输出文件合计
**16,397,838,710 B**。采样不是瞬时绝对峰值，不据此宣称与 Linux 的性能差异。

验证后同口径项目 inventory 为 **80,217,807,579 B**，超过 warning、未超过 hard；
按实际卷簇计算的文件内容分配保守上界为 **83,790,539,469 B**。
系统卷空闲 **44,104,572,928 B**，项目卷空闲 **2,694,156,300,288 B**；
磁盘空闲没有被当成项目预算。该时点在记录文档写入前，不是全程项目峰值。

## 资格范围

| 项目 | 本轮结果 |
| --- | --- |
| 原生 Windows 新 35 输入、33 providers、索引和默认向量 | PASS |
| 固定来源、完整行集 oracle、61 回执及新 runtime 绑定 | PASS |
| 四个文档命令、真实五工具、预览、续读及 inspect | PASS |
| 两次有限自然模型观察及新进程精确引用回读 | PASS，附上述引用覆盖限制 |
| 既有配置和其他服务器设置保持 | PASS |
| 完整 35 输入 fresh A/B、两套完整向量数值 A/B | NOT_RUN |
| 本轮 setup.cmd、缺依赖自动安装及安装器重复运行 | NOT_RUN |
| 广泛模型质量矩阵、答案每项 claim 的自动证明 | NOT_RUN |
| Desktop GUI 重启、持久 7.2 客户端切换 | NOT_RUN |
| 正式 release promotion、全游戏覆盖 | NOT_RUN，不由此构建授予 |

原始命令、实际退出码/耗时、逐阶段 seals、容量清单、工具参数/顺序、模型答案和卡片
仅保留在本机 ignored 验证目录。Git 只收录修复、合成测试和脱敏技术记录；
不提交游戏正文、数据库、模型、向量、个人配置或原始会话。
