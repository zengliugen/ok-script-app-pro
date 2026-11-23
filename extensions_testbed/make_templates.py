"""测试靶场的模板图截取工具（T0-3 / T0-4 用）。

**用途**：靶场运行中截取一帧客户区图像，按 `screens.py` 的元素基准坐标裁出各元素，
写入 `apps/testbed/assets/templates/<app_lang>/<screen_id>/<元素名>.png`。

**为什么需要它**：模板图必须从**实际截图**裁出，手工框选既费力又易与
`screens.py` 的坐标脱节——一旦脱节，`template` 判据的 ROI 与模板不同源，
匹配分数会莫名其妙地低，且原因不浮现。本脚本直接用 `screens.SCREENS` 的元素
矩形，使配置侧坐标与程序侧坐标**结构性地同源**
（见 `extensions_testbed/README.md` §5.4）。

**用法**（在项目根目录，靶场需已在运行）::

    uv run python extensions_testbed/make_templates.py --screen entry

本脚本是**开发工具**，不是靶场程序的一部分——它不参与靶场窗口的运行，
故与靶场模块分开命名，也不被 pytest 收集（无 `test_` 前缀 + `conftest.py` 的
`collect_ignore_glob`）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 以文件位置反推项目根与包根，使脚本可被直接运行而无需先设置 PYTHONPATH
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
for _path in (_PROJECT_ROOT / "src", _PACKAGE_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from PIL import Image  # noqa: E402  （须在 sys.path 注入之后导入）

from extensions_testbed import screens  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。

    Returns:
        解析器实例。
    """
    parser = argparse.ArgumentParser(
        prog="make_templates",
        description="从靶场运行中的实际截图裁出模板图（供适配层 template 判据使用）",
    )
    parser.add_argument(
        "--screen",
        required=True,
        help="画面 id（须在 testbed.screens.SCREENS 中），如 entry",
    )
    parser.add_argument("--app", default="testbed", help="适配层 id，默认 testbed")
    parser.add_argument("--lang", default="zh", help="目标应用语言目录，默认 zh")
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="只打印将要写入的路径，不落盘",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """截取并裁出指定画面的全部元素模板图。

    Args:
        argv: 命令行参数；None 时取 `sys.argv[1:]`。

    Returns:
        进程退出码：0 成功；1 参数或环境错误。

    Note:
        本脚本要求**靶场已在运行**（它不负责启动靶场）：靶场是单实例程序，
        若由本脚本启动，须自行处理关闭与等待窗口出现——那属启动器的职责，
        重复实现会与 `ao run` 的启动逻辑分叉。故明确要求先启动靶场。
    """
    args = build_parser().parse_args(argv)
    screen_id = args.screen

    if screen_id not in screens.SCREENS:
        available = "、".join(screens.SCREENS)
        print(f"[错误] 画面 {screen_id} 未在靶场程序中定义；当前已有：{available}", file=sys.stderr)
        return 1

    loaded = _load_app(args.app)
    if loaded is None:
        return 1

    frame = _grab(loaded)
    if frame is None:
        return 1

    out_dir = _PROJECT_ROOT / "apps" / args.app / "assets" / "templates" / args.lang / screen_id
    elements = screens.SCREENS[screen_id].elements
    if not elements:
        print(f"[错误] 画面 {screen_id} 未定义任何元素，无可截取的模板", file=sys.stderr)
        return 1

    print(f"画面 {screen_id}：共 {len(elements)} 个元素，输出目录 {out_dir.as_posix()}")
    for element in elements:
        x1, y1, x2, y2 = element.rect
        crop = frame[y1:y2, x1:x2]
        target = out_dir / f"{element.name}.png"
        if args.dry_run:
            print(f"  [空跑] {element.name}: {element.rect} → {target.name}")
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        Image.fromarray(crop).save(target, optimize=True)
        print(f"  已写出 {target.name}（{crop.shape[1]}x{crop.shape[0]}）")

    if not args.dry_run:
        print("\n完成。适配层引用写法（不写语言段与 assets 前缀）：")
        for element in elements:
            print(f"  template: {screen_id}/{element.name}.png")
    return 0


def _load_app(app_id: str):  # noqa: ANN201 - 返回 LoadedApp，避免顶层引入重依赖
    """加载适配层配置（不因「模板图尚未裁出」而失败）。

    Args:
        app_id: 适配层 id。

    Returns:
        装载结果；失败时返回 None。

    Note:
        用 `raise_on_error=False`：本脚本的用途正是**产出缺失的模板图**，
        若因模板缺失而中断，就成了「要它干活先得活干完」的死循环。
        这里只关心窗口绑定与截图配置，其余校验问题由 `ao validate` 负责报出。
    """
    from ao_config.errors import ConfigError
    from ao_config.loader import load_app

    try:
        loaded = load_app(_PROJECT_ROOT, app_id, instance="default", raise_on_error=False)
    except ConfigError as exc:
        print(f"[错误] 配置无法装载：{exc}", file=sys.stderr)
        return None
    return loaded


def _grab(loaded):  # noqa: ANN001, ANN201 - 入参为 LoadedApp，返回 np.ndarray
    """绑定靶场窗口并抓取一帧客户区图像。

    Args:
        loaded: 已装载的适配层配置。

    Returns:
        RGB 图像数组；失败时返回 None。
    """
    from ao_core.errors import EnvironmentFailure
    from ao_core.win.capture import CaptureService
    from ao_core.win.window import bind_window

    try:
        window = bind_window(loaded.app.window, timeout_s=5.0)
    except EnvironmentFailure as exc:
        print(
            f"[错误] 未找到靶场窗口：{exc.describe() if hasattr(exc, 'describe') else exc}\n"
            "        请先启动靶场（python run_testbed.py，或直接运行 main.py）",
            file=sys.stderr,
        )
        return None

    service = CaptureService(
        window,
        loaded.app.screenshot,
        content_inset=loaded.app.content_inset,
    )
    service.set_standard_resolution(loaded.app.standard_resolution)
    frame = service.grab()
    if not frame.ok:
        print(f"[错误] 抓帧失败：{frame.error}", file=sys.stderr)
        return None

    print(f"已绑定 {window.title!r}，抓帧成功（backend={frame.backend}，{frame.image.shape}）")
    return frame.image


if __name__ == "__main__":
    sys.exit(main())
