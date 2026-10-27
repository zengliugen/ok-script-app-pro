# log-noise-filter 测试策略与验收记录

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 生成器 | project-extension-capability-guard/new |
| 关联能力 | log-noise-filter |
| 创建日期 | 2026-10-26 |
| 更新日期 | 2026-10-26 |

## 测试策略

| 被测行为 | 为什么必须覆盖 | 对应用例 |
|---|---|---|
| 首次命中降级为 WARNING | 折叠不能让问题彻底隐身，必须仍有可见记录 | `TestLogNoiseFilter.py::TestNoiseFilter.test_noise_record_is_downgraded_on_first_hit` |
| 首次命中去掉 traceback 主体 | traceback 是体积来源，不去掉则折叠无意义 | `TestNoiseFilter.test_noise_traceback_removed_from_message` |
| 折叠后保留异常末行 | 只留摘要会丢掉异常类型与 pid，排查时无从判断 | `TestNoiseFilter.test_noise_keeps_exception_last_line` |
| 冷却窗口内丢弃 | 这是降噪的核心手段 | `TestNoiseFilter.test_repeated_noise_suppressed_within_cooldown` |
| 冷却期满再次报告并带计数 | 保证长期存在的变化不被永久吞掉 | `TestNoiseFilter.test_noise_reported_again_after_cooldown_with_count` |
| 非噪音 ERROR 原样放行 | 误杀真实错误是最高代价的失效模式 | `TestNoiseFilter.test_unrelated_error_passes_through_untouched` |
| INFO 级别不参与折叠 | 避免把普通日志当噪音 | `TestNoiseFilter.test_info_level_is_never_filtered` |
| 折叠计数守恒 | 放行次数与折叠计数之和等于总调用次数，避免多线程下丢计数 | `TestNoiseFilter.test_suppressed_count_conserved` |
| 安装幂等 | 重复 import / 重复 apply 不得叠加过滤器 | `TestInstall.test_install_is_idempotent` |
| 已挂载时跳过 | 覆盖 `addFilter` 去重分支，避免重复装两个过滤器导致计数翻倍 | `TestInstall.test_install_skips_when_filter_already_present` |

**刻意不测的行为**：

| 行为 | 不测的原因 |
|---|---|
| 真实 `AccessDenied` 场景下的过滤效果 | 依赖目标进程以其他账户运行，无法在 CI 稳定复现；已改用「构造同样文本的 LogRecord」等价覆盖 |
| 多线程竞态本身 | 竞态窗口仅几条字节码，在 GIL 下构造不出确定性失败；强行写并发用例只能证明「单线程首调用会写状态」，删掉锁也照样通过，属伪覆盖。改为用计数守恒守住不变量，并发正确性靠 `_admit` 内部统一持锁的代码结构保证 |
| 日志文件体积下降幅度 | 依赖运行时长与轮询频率，不是确定性指标 |

## 验收记录

| 日期 | 测试根 | 用例数 | 结论 | 环境 |
|---|---|---|---|---|
| 2026-10-26 | `extensions_tests/` | 11（本能力）+ 7（既有） | 通过 | Python 3.12 / Windows 10 19045 |
| 2026-10-26 | `tests/` | 12 | 通过 | 同上 |

> 上表两行分属两个测试根，结论不可互换：`tests/` 只说明上游基线未被破坏。

## 已知盲区

| 盲区 | 影响面 | 缓解手段 |
|---|---|---|
| 过滤器按消息子串匹配 | 上游改写日志文案后过滤器静默失效，噪音回归 | 失效表现为日志重新变多，不会误删其他日志；发现后更新 `NOISE_PATTERNS` |
| 只覆盖 `cmdline()` 一条特征 | `process.exe()` / `process.name()` 的同类异常仍按原样输出 | 这两处不常触发（需要同名权限差场景同时命中），暂不纳入；如出现再补特征 |
| 冷却窗口固定 300 秒 | 长时间运行期间的真实指标变化可能被折叠 | 折叠时带计数，冷却期满会输出「期间已折叠 N 条」，仍可判断频率 |
