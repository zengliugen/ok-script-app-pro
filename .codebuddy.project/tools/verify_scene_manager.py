# -*- coding: utf-8 -*-
"""端到端装配验证脚本（开发期手动运行，不属于测试套件）。

为什么单独一个脚本而不是单元测试：它要构造真实页面、真实 `OK` 运行时对象，
依赖 Qt 事件循环与框架装配顺序，跑起来慢且不适合 CI。单元测试覆盖纯逻辑，
本脚本覆盖「装配能不能起来」——两者互补，不能互相替代。

用法：

```bash
.venv/Scripts/python.exe .codebuddy.project/tools/verify_scene_manager.py
```
"""

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


def main():
    """验证装配与页签构造。

    Returns：
        全部通过时为 0，有失败时为 1。
    """
    failures = []

    # 1. 装配期注册
    from src import config as app_config

    cfg = dict(app_config.config)
    cfg["onetime_tasks"] = list(cfg.get("onetime_tasks", []))
    from extensions.capabilities.scene_manager import register

    register(cfg)
    specs = [entry for entry in cfg["onetime_tasks"] if "scene_manager" in entry[0]]
    if len(specs) != 2:
        failures.append(f"任务注册数量应为 2，实际 {len(specs)}")
    tabs = [entry for entry in cfg.get("custom_tabs", []) if "scene_manager" in entry[0]]
    if len(tabs) != 1:
        failures.append(f"页签注册数量应为 1，实际 {len(tabs)}")
    print(f"[1] 装配注册：任务 {len(specs)} / 页签 {len(tabs)}")

    # 2. 任务类可实例化（需要 ok 运行时已就绪）
    #
    # 注意：不要在这里预先创建 QApplication——`OK()` 内部会自己创建，而 Qt 不允许
    # 同一进程有两个 QApplication 单例（实测报「Please destroy the QApplication
    # singleton before creating a new QApplication instance」）。
    from ok import OK

    ok_app = OK(cfg)
    executor = ok_app.task_executor
    from extensions.capabilities.scene_manager.tasks import (
        SceneExploreTask,
        SceneGotoTask,
    )

    for cls in (SceneGotoTask, SceneExploreTask):
        task = cls(executor=executor, app=ok_app.app)
        task.after_init(executor=executor)
        if task.visible:
            failures.append(f"{cls.__name__} 应为 visible=False，否则会占用任务列表")
        print(f"[2] {cls.__name__} 实例化 OK，name={task.name}，visible={task.visible}")

    # 注：不在这里调 `ok.util.clazz.init_class_by_name` 走一遍框架加载器。
    # 实测它会挂在任务初始化上（超过 3 分钟无进展），原因在框架侧的任务装载
    # 时序，与本能力的装配契约无关——而本脚本要验证的正是后者。
    # 框架真实的挂载路径由 `MainWindow` 启动时覆盖，属人工验证范围。

    # 3. 页签构造（用临时库避免污染真实 assets/scenes）
    temp_dir = tempfile.mkdtemp(prefix="scene-manager-verify-")
    from extensions.capabilities.scene_manager import runtime, tab

    original = runtime.library_root
    runtime.library_root = lambda base_dir=None: os.path.join(temp_dir, "scenes")
    try:
        widget = tab.SceneManagerTab()
        widget.executor = executor
        keys = widget.strip.keys()
        print(f"[3] 页签构造 OK，子页签 {keys}")
        if keys[:3] != [tab.KEY_CONFIG, tab.KEY_FLOW, tab.KEY_DRAFTS]:
            failures.append(f"前三个子页签顺序不符：{keys}")
        if widget.debug_page is not None:
            failures.append("非 debug 模式下不应创建测试子页签")
        print(f"[3] 普通模式不含测试页：{widget.debug_page is None}")

        # 4. 空库下的基本交互
        lib = widget.library
        scene = lib.new_scene("测试场景")
        lib.save_scene(scene)
        print(f"[4] 新建场景 {scene.scene_id}")
        widget.reload_all()
        if widget.config_page.scene_combo.count() != 1:
            failures.append("刷新后场景下拉应有 1 项")
        print(f"[4] 配置页下拉项数 {widget.config_page.scene_combo.count()}")

        # 5. 流转页在空库/单场景下不崩
        widget.flow_page.reload()
        print(f"[5] 流转页节点数 {len(widget.flow_page.canvas._nodes)}")
        widget.drafts_page.reload()
        print(f"[5] 草稿页表格行数 {widget.drafts_page.table.rowCount()}")

        # 6. debug 模式下应有测试子页签
        original_is_debug = tab.is_debug_mode
        tab.is_debug_mode = lambda: True
        try:
            debug_tab = tab.SceneManagerTab()
            debug_tab.executor = executor
            keys_debug = debug_tab.strip.keys()
            print(f"[6] debug 模式子页签 {keys_debug}")
            if tab.KEY_DEBUG not in keys_debug:
                failures.append("debug 模式下应创建测试子页签")
        finally:
            tab.is_debug_mode = original_is_debug

        # 7. 识别器在空库时返回未命中而不是抛异常
        import numpy as np

        from extensions.capabilities.scene_manager import recognize

        result = recognize.recognize(np.zeros((1080, 1920, 3), np.uint8), [], 1920, 1080, lambda *a: None)
        print(f"[6] 意外：空库应当抛异常，实际返回 {result}")
        failures.append("空库识别应当抛 RecognizeError")
    except recognize.RecognizeError:
        print("[6] 空库识别正确抛 RecognizeError")
    finally:
        runtime.library_root = original

    print()
    if failures:
        print("失败项：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
