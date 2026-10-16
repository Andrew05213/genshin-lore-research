# 开发指南

## 开发环境

使用可编辑安装，以保持源码和根目录的 SQL/来源清单可以被构建器定位：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m pip install -e .
```

当前说明针对源码 checkout，不承诺独立 wheel 已包含全部外部 SQL 和配置资源。Python 3.12/Linux 已验证；其他环境需单独核对。

打包使用 setuptools 77+，以支持 `pyproject.toml` 中的 SPDX 许可表达式及许可证文件声明。

## 无游戏数据测试

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

发行包的测试覆盖组件清单、A/B 对照和失败报告处理，使用合成数据，不替代真实来源构建验证。

基础构建使用 [重建指南](guides/building-components.md)；35 个输入及统一服务使用 [完整重建指南](guides/building-dataset.md)。`docs/history/component-build-validation.json` 保留原最小验证范围，全链路结果另见 [完整重建实验](history/full-rebuild.md)，不以入口存在代替实际验证。

## 修改与验证

- 修改解析字段、SQL 或来源范围后，重新构建并独立核验受影响组件。
- 明确保留缺失、未判明、多候选和 partial 状态，不通过猜测映射补齐结果。
- 数据产物记录新的 size/SHA；旧校验记录不能用于修改后的字节。
- 新测试优先使用合成 fixture，避免提交游戏正文或完整会话。
- 部署配置不得包含个人路径、凭据或自动 fallback 到其他快照。

## 文档维护

README 负责用途、当前范围和最短入门；`docs/` 负责操作、架构、验证与许可说明；AGENTS 负责开发规则。术语首次出现时给出含义，避免用内部阶段名称代替功能描述。

文档中的命令应对应实际入口。验证范围与未完成项需分开；不要把一次组件构建成功描述为完整部署可用。公开文档不包含对话式指令、个人执行环境或维护过程流水账。

分发清单绑定文件 size/SHA。修改源码或文档后需要重新生成发行包及清单，不能仅修改 ZIP 内容并沿用旧清单。

现行命名与旧入口对应见 [命名规范](naming.md)。移动脚本时应同时核对动态 import、runpy、子进程、仓库根目录与构建身份输入；自包含脚本从根目录到子目录后，其根路径不能仍按旧层数解析。旧归档清单保留历史字节，新的归档使用自己的清单。
