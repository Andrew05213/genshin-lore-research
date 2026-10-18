# 正式 adapter 关系完整性补丁

针对最终工程审查提出的 Document 缺口，补丁修改独立 oracle，不改变构建器投影、来源范围、SQL 或 evidence 语义。

## 复现与修复

合成 Document 使用真实来源 schema 和现有索引构建函数；独立 `verify_connection` 在修复前错误接受以下两项：

- 删除全部索引 relation/candidate，将对应 coverage 改为 `unsupported/0`；
- 保留行但把已完整支持的 coverage 状态改为 `unsupported`。

仅删除候选并改写 relation 的数量时，原逐行 state/cardinality 检查已拒绝；不把已有保护误报为新的错误放行。

现在独立 oracle 使用静态关系表/FK 合同，按实际 indexed parent namespace 从**来源**计算关系及候选预期数量。候选数量不根据“剩下哪些索引关系”计算；覆盖声明、实际行数、来源分母及状态必须一致。没有调用 builder 的投影函数，也没有用抽样代替行集检查。

## 合法 partial 与测试

来源父项不属于该 adapter 的 indexed namespace 时，原合同允许 `unsupported`；仍精确核对可投影子集，不强制全表变成 complete。真正空表仍通过，候选关系和 unresolved 状态不升级。

新增七项测试：完整/空 Document、全部遗漏、候选遗漏、状态降级，以及零/部分父项范围。已有 partial fixture 原先缺 FK 列且虚报分母，现补齐真实 source rows 和 FK 列，保留原正向 partial 断言，不放宽 checker。

PR #2 本次 186 项无游戏数据测试通过；Windows 补丁及后续堆叠 PR 的最终运行见 GitHub checks。原失败与本次 before/after 日志本地保留，不覆盖历史资格记录。

验证实现 fingerprint 改变，旧 verified receipt 不能自动复用。完整 35-input 重建仍未验收；源码测试通过不替代来源重建。部署 release/current、已有数据库和历史 receipts 不变。
