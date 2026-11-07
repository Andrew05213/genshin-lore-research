> 本页的容量额度属于当时实验主机，公开副本省略具体设置，不构成项目默认限制。

# 持久来源卡与轻量研究交付

实现基线：main `2152321c77d9585b97fd936e79ff3bef71df4dd8`。
本次只改变研究交付与展示，不增加搜索模式、关系类型或 MCP 顶层工具。

## 变更

- 原五工具保留。`inspect_evidence` 增加可选 `save_source=false`；显式保存需要当前连接中实际 read 返回的文本 handle，由原正式 inspect 核验 exact ref。
- 来源卡为私有 Markdown/JSON。JSON 保留原文/SHA、observation/range、原 read mapping、本次 inspect、来源角色和候选限制；程序不判断 claim、不选另一条材料、不改写原 read 状态。
- `serve.py` research surface 与 `serve_dialogue.py` 默认选择配置目录下的 `source-cards/`；可显式指定目录。未保存不创建文件，权限或漂移失败不报告 SAVED。直接库调用须显式配置，发现接口声明是否可用。
- 卡片可在连接结束后阅读；重新核验用原 exact ref，不恢复旧 handle。原生 Codex 不被拦截，模型须把返回的真实卡片链接放到答案中。原 capture/receipt renderer 保持独立。
- 默认 describe 返回摘要及 partial 来源名称，具体表范围通过 `detail="full"` 读取；资格检查、历史差异和 full-corpus 限制不省略或升级。
- 人物面板按角色材料、来源标注发言、正文关键词、姓名与未分类项组织，并按已有标题给出下标分组。原始结果、顺序、过滤、cursor 和独立物理身份不变。

## 本次验证

Linux 合成回归：**764 passed / 3 platform-specific skipped**。随后对检查脚本新增的来源卡选项、六命令 help、MCP 及日常 handoff 运行定向检查：**26 passed**。Skill 格式、Python 编译和文档链接检查通过。Windows 原生路径交由 PR CI，不用 Linux 结果代替。

初轮全量为 760 passed / 2 failed / 3 skipped：两处完整覆盖核对仍索取默认 describe 的 components。现在显式请求 full，原 unsupported-table/coverage 断言全部保留，并新增默认摘要检查；没有删除或放宽验证。

PR 首轮 CI 的 Linux 源码检查及双端代码/依赖包检查通过。Windows 为 758 passed / 1 failed / 8 skipped，唯一失败是 Git ignore 测试的文本 stdin 在 Windows 被转成 CRLF，Git 因而把末尾 `\r` 视为路径内容并加引号。后续补丁只将该测试改成 `git check-ignore -z --stdin` 的 NUL 分隔字节协议，继续精确比较完整路径，并增加中文/空格文件名；不修改产品实现、忽略范围或失败门槛。原始 CI 失败记录保留。

实际封存 v1.1 数据通过新代码的 stdio MCP 程序化调用核对，未运行外发模型或完整数据重建：

| 范围 | 本次观察 |
|---|---|
| 人物对白 | 沃雅妮莎来源标注的 Codex 发言，连续两页无重复，选中正文保存并回读 |
| 角色故事 | 576 字物理故事正文，卡片保持完整所读文本；不代表全部角色故事 |
| Book | 从 Document 来源导航到 `Readable/CHS/Book1119.txt`，读取 19 页/38 个物理行，逐条卡片对应；仍有后文，未声明全书读完 |
| 任务相关材料 | 96 字 Native 任务相关描述；不将其冒充完整 Quest/Talk 剧情 |
| Event | 46 字有界 Event 文本，范围与来源状态保留；不扩写为完整剧情 |
| 重启 | 已保存卡片仍可读；新进程按 exact ref 回读文字/provenance 一致，旧 handle 拒绝。另对中文 Book 首页与后续页卡片做新进程回读 |
| 已有输入 | 43 个既有配置/数据文件的 device、inode、size、mtime 保持不变；无数据库/index 重建 |

最初的 Book 探针取到 Material 物品描述，仅证明该描述可保存，不计作书籍正文验收。后续明确选取 Readable，并分别保留英文局部探针及中文验证记录。原始失败、卡片和完整本地调用记录不提交公开仓库。

公开 `check_dialogue.py` 已实际检查保存后的原文/provenance。首次在沙箱中写只读 `.codex` 目录失败，记录保留；指定可写私有 `--source-cards` 目录后通过，没有提升目录权限或修改本机配置。

## 体积与时间解释

同一连接、同一正式数据的 describe 单份结构化 JSON：full **23,009 B**，默认 reading **3,708 B**，约减少 **83.9%**。这不包括 SDK 兼容 content 的重复表示，也不证明整个研究或模型总时延下降。

第一组实际 MCP 核对含两次启动/重启，总计约 53.7 秒；本次私有卡片及报告约 1 MB。项目归属口径 allocated 实测约 50.67 GB，hard 为十进制 本机私有容量额度；没有复制大型产物。

## 保留限制

程序只交付实际选择的来源，不验证解释是否成立。标题未解析、角色/归属候选、顺序未知、partial 和局部 EOF 保持。没有新增批量 inspect、speaker decoder、搜索算法或数据 coverage，也未修改旧 release、receipt、current 或正式 evidence ref。

实际使用和参数见 [对白阅读指南](../guides/reading-dialogue.md#保存可回查的来源卡)。已有部署仍须显式更新运行代码；本次没有切换本机固定入口，也不声称新真实模型答案验收完成。来源卡只属于保存时的本地证据交付，不成为新的 corpus authority。
