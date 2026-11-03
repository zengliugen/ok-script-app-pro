# -*- coding: utf-8 -*-
"""扩展翻译域的挂载实现。

框架把 `ok` 翻译域的对象放在 `app.po_translation` 上，且**懒加载**：只有在它为
`None` 时才去读 `i18n/<locale>/LC_MESSAGES/ok.mo`。因此在 `my_app` 构造期把这个
属性换成「扩展域 → 上游域」的链，就能在不改上游目录的前提下让扩展文案生效。

为什么不是新增语言基础码目录（`i18n/en/`、`i18n/zh/`）：那只能追加不能覆盖，
且 `zh_TW` 的回退链是 `['zh_TW', 'zh']`，会把简体译文给到繁体用户。
详见 `extensions_docs/design/extension-i18n.md`。

依赖的框架内部符号只有一个：`App.po_translation`。它消失时立即抛错并指名，
不静默降级——降级的后果是「扩展文案永远显示原文」，比启动失败更难发现。
"""

import gettext
from pathlib import Path

from ok.core.translation import get_translations
from ok.util.i18n import get_language_fallbacks

# 扩展自有域名。与上游 `ok` 域隔离，合并上游时零冲突。
DOMAIN = "ok_ext"

# 译文目录相对能力包的固定位置：`<能力包>/i18n/<locale>/LC_MESSAGES/ok_ext.mo`。
CATALOG_DIR_NAME = "i18n"


def install(app, capabilities_root=None):
    """把扩展翻译链挂到 app 上，返回是否真的接管。

    Args：
        app：框架的 App 或 HeadlessApp 实例，需带 `po_translation` 与 `locale`。
        capabilities_root：能力包根目录；默认取本模块的两级父目录。

    Returns：
        True 表示已接管 `app.po_translation`；False 表示没有任何扩展译文，
        保持框架原有行为（此时不做任何修改）。

    Raises：
        RuntimeError：`app` 上不存在 `po_translation`，说明框架已改动该注入点。
    """
    if not hasattr(app, "po_translation"):
        raise RuntimeError(
            "扩展翻译接管失败：App 上不存在 po_translation 属性，"
            "请检查 ok-script 升级后更新 extensions/capabilities/extension_i18n/translator.py"
        )

    locale_name = _locale_name(app)
    if capabilities_root is None:
        capabilities_root = Path(__file__).resolve().parent.parent
    catalog_dirs = find_catalog_dirs(capabilities_root)
    if not catalog_dirs:
        return False

    translation, took_over = build_translation(locale_name, catalog_dirs)
    if not took_over:
        return False

    translation.install()
    app.po_translation = translation
    return True


def find_catalog_dirs(capabilities_root):
    """列出所有能力包内的译文目录，按目录名字典序返回。

    Args：
        capabilities_root：`extensions/capabilities` 目录。

    Returns：
        存在的 `<能力包>/i18n` 目录列表；不存在时为空列表。
    """
    root = Path(capabilities_root)
    if not root.is_dir():
        return []
    dirs = []
    for package in sorted(root.iterdir()):
        if not package.is_dir() or package.name == "__pycache__":
            continue
        catalog = package / CATALOG_DIR_NAME
        if catalog.is_dir():
            dirs.append(catalog)
    return dirs


def build_translation(locale_name, catalog_dirs, upstream=None):
    """构造「扩展域 → 上游域」的翻译链。

    按传入顺序逐个叠加，**后叠加的处于链顶**（gettext 的 `add_fallback` 语义是
    「当前对象优先，参数兜底」）。同一 msgid 出现在两个能力包里属设计错误，
    由体检脚本单独报告——这里不静默裁决，只用确定的顺序兜底。

    Args：
        locale_name：语言名，如 `zh_CN`。
        catalog_dirs：译文目录列表，顺序即叠加顺序。
        upstream：上游翻译对象；默认现场读取 `ok` 域，测试可注入替身。

    Returns：
        (翻译对象, 是否含扩展条目)。没有任何扩展条目时第二项为 False，
        此时返回的仍是上游对象。
    """
    translation = upstream if upstream is not None else get_translations(locale_name)
    took_over = False
    for catalog_dir in catalog_dirs:
        extended = gettext.translation(
            DOMAIN,
            str(catalog_dir),
            languages=get_language_fallbacks(locale_name),
            fallback=True,
        )
        if not _has_entries(extended):
            continue
        extended.add_fallback(translation)
        translation = extended
        took_over = True
    return translation, took_over


def _has_entries(translation):
    """判断翻译对象是否真含条目。

    `fallback=True` 且文件不存在时，`gettext.translation` 返回空
    `NullTranslations`——它没有 `_catalog`，加进链只会多一层转发。

    Args：
        translation：gettext 翻译对象。

    Returns：
        布尔值。
    """
    return bool(getattr(translation, "_catalog", None))


def _locale_name(app):
    """取出 app 的语言名。

    Args：
        app：框架 App 实例。

    Returns：
        语言名字符串；`locale` 缺失时按 `en_US` 处理。
    """
    locale = getattr(app, "locale", None)
    if locale is None:
        return "en_US"
    if hasattr(locale, "name"):
        return locale.name()
    return str(locale)
