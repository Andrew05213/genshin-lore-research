# 完整有界数据重建

`scripts/rebuild_complete.py` 将固定来源重新解析为 35 个独立输入、Schema20 的 33 个 core providers 和统一 Research 索引。它不使用已部署数据库，不复制历史验收凭据，也不生成全游戏覆盖声明。

当前真实执行结果见 [完整重建实验](COMPLETE_REBUILD_EXPERIMENT.md)。原 14 类基础组件入口和失败记录仍保留，不能代替完整链路的结果。

## 输入

安装方式及 7.1 源码取得方式见 [基础重建指南](REBUILDING.md)。另需历史来源快照，仅供原有 Event/Review 字段连续性 oracle 核对：

```sh
git -C data/AnimeGameData worktree add --detach ../AnimeGameData-history \
  26df1dfbdf05a82bbb1d97506859f3e1c40718d8
```

两个 checkout 都须保持干净。历史源码不是旧版数据库，不参与当前游戏文本查询；不能用 7.1 源码替代它而跳过原校验。

## 容量

先统计整个项目、相关来源、输出和缓存的 allocated bytes，按 device/inode 去重。当前政策为 hard `90,000,000,000 B`，warning `77,309,411,328 B`。hard headroom 是 hard 减去当前项目占用，不能用主机空闲空间代替。

全链路要求至少 25 GB 新增峰值预算，另检查输出文件系统的可用空间。该预算不包含尚未取得的上游 checkout、依赖或模型下载。统一索引构建及完整 oracle 使用较多 RAM；当前验证环境有约 16 GB RAM 和 4 GB swap。增加并行构建会改变峰值，不属于此入口的容量保证。

## 构建

以下 `HEADROOM_BYTES` 应填入刚完成的项目容量实测余量：

```sh
.venv/bin/python scripts/rebuild_complete.py \
  --source data/AnimeGameData \
  --historical-source data/AnimeGameData-history \
  --output-root data/rebuilt \
  --available-allocated-bytes HEADROOM_BYTES \
  --stage all
```

构建顺序为 core、Character、NPC/Dialog/Reminder、独立文本组件、Quest/Talk、Codex、R0、Readable/Subtitle、Event、Native 和统一索引。生成的依赖绑定只引用本次输出；来源读取失败、scope 漂移或 seal 错配时停止，不寻找其他部署的数据库。

每个阶段保留 `invocation.json`、`build.json`、`verify.json`、`verified.json` 与 artifact。失败不会产生成功 receipt；诊断记录保留于该阶段目录。已有成功阶段允许按 seal 恢复，未核验的产物必须先通过原独立 verifier。不要在恢复期间修改源码、scope 或输入绑定；变更后的重建使用新源码目录和输出目录。

`--stage` 可用于定位阶段，但不是完整通过的替代。原始失败目录须保留；不能覆盖旧 artifact 或删除失败报告后宣称首次通过。

## 启动

成功后生成 `data/rebuilt/runtime-config.json`，并执行 runtime smoke。还可单独检查或启动 stdio MCP：

```sh
.venv/bin/python scripts/rebuilt_runtime.py \
  --config data/rebuilt/runtime-config.json --check

.venv/bin/python scripts/rebuilt_runtime.py \
  --config data/rebuilt/runtime-config.json
```

默认 surface 复用原五工具：describe/search/read/follow/inspect。加 `--surface narrative` 复用独立 query/inspect 便利接口及 Book/Story 续读。客户端通过上述 stdio 命令连接；源码安装和数据构建不自动修改个人 Codex 配置。

五工具请求使用生成 profile 中的 `corpus_ref`。此来源核验入口尚未晋级为正式 release；便利接口的自然问题应明确写出 `7.1`，如“7.1这本《终北祷歌·上》讲什么？”。它不提供其他版本，也不继承原部署的日常默认配置。

生成配置绑定本次 exact artifacts、parser/run、来源归档和索引。其 `accepted` 历史字段仅满足已有 prepublication 协议，`product_accepted=false`，不是正式 release 晋级，也不是复制原部署的 acceptance。

程序化检查可启动真实 stdio 客户端，验证工具发现、五工具调用和 Book/Story 续读：

```sh
.venv/bin/python scripts/check_rebuilt_mcp.py \
  --config data/rebuilt/runtime-config.json
```

该检查不调用语言模型，不代表模型自主研究或最终答案质量验收。

## 范围

- 重建的是固定 7.1 的有界数据产品及 literal Research 服务，不是全部游戏数据。
- 8 项 historical contract gaps 和 12 条 component gap records 不因重建消失；候选、未判明关系及有限 Event 范围保留。
- semantic/embedding overlay 不包含在该完整数据入口中，不能将 literal 启动成功写成 semantic 已验证。
- 新来源绑定和代码会产生新的 parser/run 与 seal。完整来源正文和坐标核验，比复写旧数据库 SHA 更重要；不声称跨目录、跨 Python/SQLite 版本的数据库字节完全相同。
- 生成数据库、来源正文和包含路径的绑定均不提交 Git。静态 scope 与重建 recipe 保持 tracked；本次运行配置为 ignored 本地产物。
