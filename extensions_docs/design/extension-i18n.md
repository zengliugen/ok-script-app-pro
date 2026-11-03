# extension-i18n 设计

| 字段 | 值 |
|---|---|
| 状态 | 已实施 |
| 关联能力 | extension-i18n |
| 创建日期 | 2026-11-03 |
| 更新日期 | 2026-11-03 |
| 生成器 | devflow-guard/new |

## 背景

> 需求已确认，探索记录见 explore/extension-i18n.md（含已排除方向与重新可行的前提）。

框架的 `ok` 翻译域存放于 `i18n/<locale>/LC_MESSAGES/ok.po`，**该目录属上游文件**。
扩展能力若新增界面可见文案，要翻译就只能改这个上游目录：合并上游时冲突，
且扩展条目与上游条目混在同一份文件里无法归属。`extension-i18n` 提供一条
「不改上游也能翻译扩展文案」的通道，并把「什么时候必须做本地化」接进体检与提交门禁。

## 要解决的问题

- **现象**：扩展能力新增任务名、Tab 名、配置键与选项后，界面在非源语言下仍显示原文；
  要修只能改上游 `i18n/` 目录。
- **现象**：即使建好翻译目录，也没有任何检查会发现「代码里有界面文案、目录里没有对应条目」，
  也没有检查会发现「改了 `.po` 忘了编译 `.mo`」——后者会让翻译改了却完全不生效，且不报错。
- **不做的后果**：扩展能力要么永久不做本地化，要么每次都动上游文件并登记差异。
  能力越多，这两种代价越大。

## 候选方案

至少两个候选，必须包含「什么都不做」。

| 方案 | 做法 | 代价 | 前提 |
|---|---|---|---|
| **A 自有域 + fallback 挂载（选定）** | 扩展建 `ok_ext` 域；在 `my_app` 钩子里预置 `app.po_translation` 为「扩展域 → 上游域」的链 | 需挂载代码；依赖 `po_translation` 这一内部属性（加断言） | `po_translation` 在 `my_app` 创建前为 `None` 且懒加载（已核实） |
| B 新增语言基础码目录（`i18n/en/`、`i18n/zh/`） | 利用 `get_language_fallbacks` 让 `en`/`zh` 进入查找链 | 只能追加不能覆盖；`zh_TW` 回退链为 `['zh_TW','zh']`，会把简体扩展译文给繁体用户 | 无（框架现状即支持） |
| 不做 | 扩展文案写死，或改上游 `ok.po` | 前者无法本地化；后者每次合并上游都冲突并需登记差异 | 无 |

**B 在什么前提下重新可行**：扩展不需要覆盖任何上游译文，且可以接受繁体用户看到简体扩展译文。
一旦出现「扩展要改写上游某条译文」的需求，B 立刻不成立。

**不做的方案在什么前提下重新可行**：项目停止跟进上游（不再 `git merge upstream/master`）时，
直接改上游 `i18n/` 反而更简单。

## 实现手段

按依赖深度从浅到深：显式扩展点 → 继承覆写 → 观察事件 → 侵入式替换。

- **选定哪一级**：路径 1 + 路径 3 的组合——能力包 `register()`（路径 1 的装载契约）
  加 `my_app` 生命周期钩子 `on_app_ready`（路径 3 的事件订阅）。
- **若上升过级别**：没有上升。路径 1 的 config 注册项里没有「翻译对象」这一可注册槽位，
  因此需要路径 3 的钩子来获得 `og.app` 就绪的时刻；两者都在零改动范围内，
  不涉及继承覆写（路径 2）与 patch（路径 4）。

**为什么不用路径 4（patch）**：`ok.core.translation.get_translations` 是模块级函数，
patch 它可以实现同样的效果，但它挂在框架内部符号上，改名即静默失效；
而 `po_translation` 是实例属性，预置它是**使用**而非**改写**框架行为，风险低一级。

## 实测证据

验证关键假设时跑过的操作与结果。**可复现是硬要求**——后人要能重跑一遍确认结论仍然成立。

```text
# 1. 语言回退展开（决定「基础码目录」方案是否可行）
$ .venv/Scripts/python.exe -c "from ok.util.i18n import get_language_fallbacks; ..."
en_US → ['en_US', 'en']     zh_CN → ['zh_CN', 'zh']     zh_TW → ['zh_TW', 'zh']
gettext.find(all=True) 实测命中目录：zh_TW → ['zh', 'zh_CN']
# 结论：zh_TW 会先命中 zh/，后命中 zh_CN/ —— 简体译文会泄漏给繁体用户

# 2. 空 msgstr 的遮蔽语义
GNUTranslations.gettext: _catalog.get(message, missing) —— 命中即返回，空串也返回
# 结论：扩展 catalog 里留空 msgstr 会遮蔽上游译文，必须禁止

# 3. 翻译对象的注入时机
ok/__init__.py:318  self.po_translation = None      ← App.__init__
ok/__init__.py:319  og.app = self
ok/__init__.py:324  my_app = init_class_by_name(...)  ← 晚于 og.app
ok/__init__.py:351  if self.po_translation is None:  ← 懒加载，预置即接管
HeadlessApp 同构：:478 / :500 / :501 / :510
# 结论：在 my_app 构造期预置 po_translation 可接管，且不早于任何 tr() 调用

# 4. polib 可用性
$ .venv/Scripts/python.exe .../task_i18n_helper.py check --i18n i18n
环境不可用：未安装 polib，无法读写 .po 文件。   （退出码 3）
$ grep -rn "msgfmt|polib" pyappify.yml .github/workflows/*.yml   → 无命中
# 结论：构建链不编译 .po，且本机无 polib —— 校验与编译必须零依赖

# 5. 自写 .mo 能否被标准库解析
用 struct 手写 mo（含 metadata 条目）→ gettext.GNUTranslations(io.BytesIO(...)) 解析通过
# 结论：零依赖编译可行

# 6. 现有 po 与 mo 是否一致（改造前的基线）
标准库解析 po 文本 + mo 二进制对比：
en_US po=86 mo=86 only_po=0 only_mo=0 diff=0
zh_CN po=86 mo=86 only_po=0 only_mo=0 diff=0
# 结论：当前一致，接入检查不会误报

# 7. 扩展资源目录是否被包结构检查误报
check_package_structure 对 capabilities/demo/i18n/zh_CN/LC_MESSAGES 报：
  [错误] 包结构：... 缺少 __init__.py，会退化为命名空间包
# 结论：必须为 i18n 子树加排除，否则能力包一放译文体检就红
```

环境前提：Windows 10、Python 3.12.9、ok-script 2.0.0b7、`.venv` 无 polib。

## 结论

选方案 A。理由：

1. **能覆盖上游译文**——扩展有能力改写任意条目，B 不能；
2. **域隔离**——`ok_ext` 与 `ok` 完全分开，合并上游时零冲突；
3. **落位合规**——译文按能力包存放（`capabilities/<能力>/i18n/`），与「能力自包含」一致，
   删能力时译文随之消失；
4. **失败可失效**——只依赖 `po_translation` 一个属性，加断言后框架改名会立即崩溃并指名文件，
   不会静默失效。

同时确立三条配套约束：`.po` 与 `.mo` 都入库（与上游一致，构建链不动）、
catalog 内不得留空 `msgstr`（会遮蔽上游）、同一 msgid 不得出现在两个能力包里（顺序会静默决定胜负）。

## 未解问题

设计阶段无法确定、需要实施中验证的点。这些会进实施偏差记录。

- 多能力包 catalog 的加载顺序：按目录名字典序拼接，后者覆盖前者。需用测试固定该行为，
  并在规范里禁止跨包重复 msgid。
- `extensions/` 下资源目录的排除判据：是「路径含 `i18n` 段」还是「路径含 `LC_MESSAGES` 段」。
  实施时取更窄的一种，避免误放过真正的包。
- 挂链失败（例如裁剪版 `ok` 没有 `po_translation`）时的行为：断言崩溃，还是降级为纯上游？
  倾向崩溃（规范要求「侵入性改动必须可失效」），但要在测试里固定。
