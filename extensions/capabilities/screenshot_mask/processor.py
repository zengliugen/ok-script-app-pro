# -*- coding: utf-8 -*-
"""截图处理器：把配置里的遮罩几何应用到每一帧。

上游 `screenshot_processor` 是一个回调：截图层在拿到帧后调用它，用返回值替换原帧
（`ok/core/screenshot.py` 与 `TemplateTab.save_template_screenshot` 两处都这么用）。
因此本能力的生效方式是**替换这个回调**，不需要改上游任何文件。

两个刻意的设计：

- **开关与异常都在这里统一判**。几何层不判开关，避免「关掉开关仍被涂黑」时分不清是
  配置没读到还是几何层忽略了开关。异常一律吞掉并写日志：遮罩是装饰性后处理，
  它失败不该让截图、模板采集与任务整条链路跟着失败。
- **每次调用都重新读配置**。处理器是常驻回调，用户可能在页签里改了但没重启；
  缓存配置会让「保存了但没生效」成为一个只能靠重启绕过的故障。
"""

from ok.util.file import get_relative_path, read_json_file, write_json_file
from ok.util.logger import Logger

from extensions.capabilities.screenshot_mask import geometry

logger = Logger.get_logger(__name__)

# 全局配置项名。同时是设置页分组标题、配置文件名（`configs/<名>.json`）与
# `get_config` 的查找键，三处共用同一个常量。
OPTION_NAME = "Screenshot Mask"

OPTION_DESCRIPTION = "Draw a black rectangle over part of every screenshot."


def build_config_option():
    """构造本能力的全局配置项。

    只暴露「是否启用」一项：位置与尺寸由页签上的可视化拖拽承担，把四个数值也铺到设置页
    会让用户面对「改了数字但看不到效果」的编辑方式。

    配置键会原样成为界面标签与持久化字段，因此必须是稳定的 ASCII 英文；中文键会让
    已有配置在日后无法安全改名，体检也直接判错。

    Returns：
        :class:`ok.ConfigOption` 实例。
    """
    from ok import ConfigOption

    return ConfigOption(
        OPTION_NAME,
        dict(geometry.DEFAULT_VALUES),
        description=OPTION_DESCRIPTION,
        config_description={
            geometry.KEY_ENABLED: "Draw the mask on screenshots.",
        },
        # 四个数值键与锚点由页签写入，不在设置页渲染：它们没有独立语义，
        # 脱离画面拖拽去编辑数字只会产生歧义。`hidden` 是框架过滤界面的正式语义。
        config_type={
            geometry.KEY_ANCHOR: {"hidden": True},
            geometry.KEY_WIDTH: {"hidden": True},
            geometry.KEY_HEIGHT: {"hidden": True},
            geometry.KEY_OFFSET_X: {"hidden": True},
            geometry.KEY_OFFSET_Y: {"hidden": True},
        },
    )


def migrate_legacy_config(folder=None):
    """把旧版中文键的配置改写为新版英文键。

    旧实现用中文键持久化（`启用遮罩`、`遮罩位置`、`宽度`…）。键名换成英文后，框架的
    `verify_config` 会把旧键当作未知字段丢弃，用户调好的数值就一起消失。因此必须在
    `Config` 对象构造**之前**改写文件——构造之后再改，丢掉的键已经拿不回来了。

    Args：
        folder：配置目录；缺省取上游默认的 `configs`。

    Returns：
        迁移后的键数；文件不存在或无需迁移时返回 0。
    """
    path = get_relative_path(folder or "configs", OPTION_NAME + ".json")
    try:
        raw = read_json_file(path)
    except Exception as error:  # 读失败不阻断启动，遮罩会退回默认值
        logger.warning(f"读取截图遮罩旧配置失败，按默认值处理：{error}")
        return 0
    if not isinstance(raw, dict):
        return 0

    migrated = {}
    changed = 0
    for key, value in raw.items():
        new_key = geometry.LEGACY_KEYS.get(key)
        if new_key is None:
            migrated[key] = value
            continue
        changed += 1
        if new_key == geometry.KEY_ANCHOR:
            value = geometry.LEGACY_ANCHORS.get(value, value)
        migrated[new_key] = value

    if not changed:
        return 0

    # 键名与锚点取值都可能是旧的，统一补齐缺省：迁移只保证「用户调过的值不丢」，
    # 不保证旧文件里每个新键都有对应来源。
    for key, value in geometry.DEFAULT_VALUES.items():
        migrated.setdefault(key, value)
    # 旧版的「数值单位」键没有新对应物（单位固定百分比）。它存的是像素时，数值本身
    # 仍是像素量级，直接当百分比用会把遮罩放大到铺满画面，因此丢弃并留日志提示重调。
    unit = raw.get("数值单位")
    if unit and unit != "百分比":
        logger.warning(
            "截图遮罩旧配置记录的单位是「" + str(unit) + "」，新版本固定使用百分比，"
            "请在「截图遮罩」页签中重新调整遮罩范围"
        )
    migrated.pop("数值单位", None)

    try:
        write_json_file(path, migrated)
    except Exception as error:
        logger.warning(f"写回截图遮罩迁移配置失败，按默认值处理：{error}")
        return 0
    logger.info(f"截图遮罩配置已迁移到英文键，共 {changed} 项：{path}")
    return changed


def make_processor(config_getter=None):
    """构造 `screenshot_processor` 回调。

    Args：
        config_getter：返回当前遮罩配置字典的可调用对象；缺省用 `og.global_config`。
            注入点是给测试用的——真实运行时不需要传。

    Returns：
        接收一帧、返回一帧的回调。
    """

    def process(frame):
        """按当前配置在帧上涂黑遮罩区域。

        Args：
            frame：截图层传入的帧（已是副本）。

        Returns：
            处理后的帧；任何异常或配置缺失都返回原帧。
        """
        if frame is None:
            return None
        try:
            values = _current_values(config_getter)
            if values is None or not values.get(geometry.KEY_ENABLED, False):
                return frame
            return geometry.apply_mask(frame, values)
        except Exception as error:
            # 遮罩失败不得中断截图：原帧直接返回，问题留在日志里。
            logger.warning(f"截图遮罩应用失败，本次截图不遮罩：{error}")
            return frame

    return process


def _current_values(config_getter):
    """读取当前的遮罩配置。

    Args：
        config_getter：注入的读取器；为空时从框架的全局配置取。

    Returns：
        配置字典；配置项未注册（例如测试环境或装配异常）时返回 ``None``。
    """
    if config_getter is not None:
        return config_getter()

    from ok import og

    global_config = getattr(og, "global_config", None)
    if global_config is None:
        return None
    try:
        return global_config.get_config(OPTION_NAME)
    except RuntimeError:
        # 配置项尚未注册：能力未装配或被测试单独引入，直接不遮罩。
        return None
