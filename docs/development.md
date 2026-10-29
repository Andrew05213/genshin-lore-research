# 开发指南

## 开发环境

项目明确采用**源码 checkout + 可编辑安装**，以保持源码和根目录的 SQL/来源清单可以被构建器定位：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
```

不承诺独立 wheel 包含外部 SQL、来源 scope、recipe 或公开脚本；不要将 wheel 安装成功当作独立部署成功。六个公开命令从源码根目录执行，不另注册一套同名 console scripts。Python 3.12 的 Linux/Windows 合成检查分别由 CI 执行；平台完整来源构建需单独记录，见 [Windows 指南](guides/windows.md)。

打包使用 setuptools 77+，以支持 `pyproject.toml` 中的 SPDX 许可表达式及许可证文件声明。

## 无游戏数据测试

```sh
.venv/bin/python -m pytest -q --cov=scripts.build --cov=scripts.build_workers --cov=scripts.build_support \
  --cov=scripts.runtime_config --cov=genshin_lore.mcp_transport \
  --cov=genshin_lore.dialogue --cov-report=term-missing --cov-report=xml
.venv/bin/python -m compileall -q src scripts tests
.venv/bin/python -m build --wheel --no-isolation
```

测试覆盖验证状态拒绝、恢复身份、报告完整性、双输出隔离、真实 MCP 子进程、对白 cursor、引用映射、文档链接和 Apache-2.0 元数据。全部使用合成数据，不替代真实来源构建验证。覆盖率仅统计上述关键模块，不设任意百分比门槛，也不宣称全库覆盖。

`.github/workflows/ci.yml` 在 PR、main push 和手动触发时执行这些检查及六个公开命令的 `--help`、wheel 元数据核对。CI 不下载游戏来源，不读取个人数据库或凭据。源码测试、来源 oracle、正式 release 验收是三种不同资格。

`requirements-build.txt` 与包的直接依赖一致；`requirements-dev.lock` 固定 Python 3.12 开发/CI 的传递依赖、平台条件及下载 SHA。更新依赖后在干净环境重测；可用 `uv pip compile requirements-dev.txt --universal --generate-hashes --python-version 3.12 --default-index https://pypi.org/simple --output-file requirements-dev.lock` 更新锁文件。semantic 的 `fastembed` / `hnswlib` 留在可选 extra；默认完整构建需安装，基础 CI 用合成向量 fixture 而不下载权重，见 [向量构建](guides/building-semantic.md)。

MCP 兼容层只支持已验证的 `mcp==2.1.1`；升级必须重跑 stdio 注册、请求、分页和引用测试。私有接口集中在 `mcp_transport.py`，不是业务模块的可用扩展接口。公开 SDK stdio 在本次执行环境的最小 initialize 探针超时，因此没有未经验证地替换现行传输。

基础构建使用 [重建指南](guides/building-components.md)；35 个输入及统一服务使用 [完整重建指南](guides/building-dataset.md)。`docs/history/component-build-validation.json` 保留原最小验证范围，全链路结果另见 [完整重建实验](history/full-rebuild.md)，不以入口存在代替实际验证。

## 修改与验证

- 修改解析字段、SQL 或来源范围后，重新构建并独立核验受影响组件。
- 新构建的动态绑定、profile 和资格记录属于该输出目录的 `metadata/`，不得写回源码合同。不同输出不共享绑定；并行测试不代表大数据并行峰值已获保证。
- 已有 `verified.json` 仅在 v3 回执、阶段代码域/全部静态合同/环境/显式依赖/参数及全部记录 seal 均匹配时恢复。旧回执或失败目录拒绝自动恢复，保留诊断并选择新输出；文档修改不触发重建。显式代码域、内存权衡与本次同条件测量见 [构建复用](build-reuse.md)。
- 明确保留缺失、未判明、多候选和 partial 状态，不通过猜测映射补齐结果。
- 数据产物记录新的 size/SHA；旧校验记录不能用于修改后的字节。
- 新测试优先使用合成 fixture，避免提交游戏正文或完整会话。
- 部署配置不得包含个人路径、凭据或自动 fallback 到其他快照。

## 文档维护

README 负责用途、当前范围和最短入门；`docs/` 负责操作、架构、验证与许可说明；AGENTS 负责开发规则。术语首次出现时给出含义，避免用内部阶段名称代替功能描述。

文档中的命令应对应实际入口。验证范围与未完成项需分开；不要把一次组件构建成功描述为完整部署可用。公开文档不包含对话式指令、个人执行环境或维护过程流水账。

分发清单绑定文件 size/SHA。修改源码或文档后需要重新生成发行包及清单，不能仅修改 ZIP 内容并沿用旧清单。

现行命名与旧入口对应见 [命名规范](naming.md)。移动脚本时应同时核对动态 import、runpy、子进程、仓库根目录与构建身份输入；自包含脚本从根目录到子目录后，其根路径不能仍按旧层数解析。旧归档清单保留历史字节，新的归档使用自己的清单。

工程整改的实际执行、未运行的高成本验证与依赖分类见 [工程质量记录](engineering-quality.md)。不以全仓格式化、批量重命名历史模块或共享构建投影实现来替代独立验证。
