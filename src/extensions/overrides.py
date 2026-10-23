# -*- coding: utf-8 -*-
"""config 值覆写。

集中声明本项目对上游 config 的所有值覆写。**不允许**在这里新增上游没有的
顶层结构——新增结构会与上游演进冲突；只做既有键的值替换。

缺键语义：启动时先收集全部缺失项，一次性抛出，不在第一个缺失处中断。
上游若改多处，一次就能暴露全部受影响项。
"""

# 覆写表：键路径 → 期望值。键路径用点号表示嵌套。
OVERRIDES = {
    "links.default.github": "https://github.com/zengliugen/ok-script-app-pro",
    "links.default.share": "Download from https://github.com/zengliugen/ok-script-app-pro",
    "links.default.faq": "https://github.com/zengliugen/ok-script-app-pro",
    "gui_title": "ok-script-app-pro",
}

_SEP = "."


def apply(config):
    """应用全部覆写，缺键时一次性列出并抛错。

    参数：
        config: 应用配置字典

    异常：
        RuntimeError: 存在覆写键在上游 config 中不存在
    """
    missing = [path for path in OVERRIDES if not _has_path(config, path)]
    if missing:
        raise RuntimeError(
            "上游 config 结构已变化，以下覆写键不存在：\n  "
            + "\n  ".join(missing)
            + "\n请检查 upstream 变更后更新 src/extensions/overrides.py"
        )
    for path, value in OVERRIDES.items():
        _set_path(config, path, value)


def _has_path(config, path):
    """判断点号路径在字典中是否可解析。"""
    node = config
    for key in path.split(_SEP):
        if not isinstance(node, dict) or key not in node:
            return False
        node = node[key]
    return True


def _set_path(config, path, value):
    """按点号路径写入值；调用前须已确认路径存在。"""
    keys = path.split(_SEP)
    node = config
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
