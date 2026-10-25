# patch 目录

存放对框架的 monkey patch，**一个目标一个文件**。

## 准入条件

patch 是最后手段。新增 patch 前必须确认规则 `extension-capability/methods.mdc` 中路径 1、2、3 都不可行，
并在差异登记条目里写明各自失败的原因。

## 文件约定

每个 patch 模块必须提供 `apply()` 函数，且满足：

1. 开头断言目标符号存在，缺失即抛 `RuntimeError` 并指名。
2. 幂等：重复执行无副作用。
3. 保留原实现引用，便于排查与恢复。

## 当前状态

无 patch。
