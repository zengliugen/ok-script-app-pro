# -*- coding: utf-8 -*-
"""生成场景管理能力的中英 gettext catalog（开发期工具，不属于运行时代码）。

用法：`.venv/Scripts/python.exe .codebuddy.project/tools/gen_scene_manager_po.py`

**源串一律英文**：脚本只收集 `tr("...")` 里的英文字面量，`msgid` 因此也是英文；
中文只出现在 `zh_CN` 的 `msgstr` 里，`en_US` 的 `msgstr` 与 `msgid` 相同。这条口径的
理由见 i18n 技能的 `references/catalog-rules.md` §五（中文当源串会让缺译时回退成中文）。

为什么用脚本而不是手写 po：界面文案接近百条，手写容易漏；而漏一条的表现是
「某处界面显示英文原文」，很难在界面上逐处核对。脚本从源码里抓调用点，保证不漏。

**动态文案不进 catalog**：状态栏提示、错误消息、日志由 f-string 拼出，没有固定
`msgid` 与之对应（见 catalog-rules §二），一律保持中文原文。
"""

import json
import pathlib
import re

BASE = pathlib.Path(__file__).resolve().parents[2]
CAPABILITY = BASE / "extensions" / "capabilities" / "scene_manager"

# 唯一的抓取口径：`tr("...")` 的实参。所有界面文案都必须经 `tr()` 包一层，
# 这样「哪些串要翻译」与「哪些串不需要」在源码上就是可见的，不靠脚本猜。
TRANSLATE_CALL = re.compile(r"\btr\(\s*\"([^\"\n]+)\"\s*\)")
# 多行形式的 `tr(\n    "第一段"\n    "第二段"\n)`：相邻字符串字面量会由 Python 自动拼接，
# 脚本也要按同样规则合并，否则 msgid 对不上。
TRANSLATE_MULTILINE = re.compile(r"\btr\(\s*((?:\s*\"[^\"\n]*\"\s*)+)\)", re.S)
STRING_LITERAL = re.compile(r"\"([^\"\n]*)\"")
# `MODE_LABELS = ((MODE_SELECT, "Select"), ...)`：显示名在元组里，调用处是
# `tr(text)` 传变量，静态抓不到。必须单独收，否则这三个按钮永远不翻译。
MODE_LABEL_TABLE = re.compile(r"\(\s*MODE_\w+,\s*\"([^\"\n]+)\"\s*\)")
# 同形态的另三张表：探索动作、滑动方向、动作参数。
#
# 它们与 MODE_LABELS 是同一类问题——显示名在元组/字典里、调用处 `tr(label)` 传
# 变量。用一个更宽的规则统一收，但**必须限定在显式的表定义块内**：裸的
# `"键": ("文本", ...)` 规则会误收 `_MASK_ANCHOR_SIDES` 这类内部映射，把
# `bottom_right`、`center` 这些枚举值当成界面文案塞进 catalog。
LABEL_TABLE_BLOCK = re.compile(
    r"^(EXPLORE_ACTION_LABELS|SWIPE_DIRECTION_LABELS|ACTION_PARAM_LABELS)\s*=\s*[\([{](.*?)^[\)\]}]",
    re.S | re.M,
)
LABEL_PAIR_TABLE = re.compile(r"\(\s*[\w.]+,\s*\"([^\"\n]+)\"\s*\)")
PARAM_LABEL_TABLE = re.compile(r"^\s*\"\w+\":\s*\(\s*\"([^\"\n]+)\"", re.M)
# 任务描述不经 `tr()` 包一层——框架自己在任务列表里对 `task.description` 调 `app.tr()`。
# 门禁按 `self.description` 收集，脚本必须同样收，否则「源码有、catalog 缺」。
TASK_DESCRIPTION = re.compile(r"self\.description\s*=\s*\"([^\"\n]+)\"")

# 任务配置项的键。框架把键本身当界面标签、把说明当副标题，两者都过 `app.tr()`，
# 因此键与说明都必须进 catalog——漏一条的表现是任务配置界面里某一行显示英文。
CONFIG_KEY_PATTERN = re.compile(r'^\s*"([^"]+)":\s*(?:"|[\d.])', re.M)
CONFIG_GET_PATTERN = re.compile(r'self\.config\.get\(\s*"([^"]+)"')
# `config_description.update({...})` 里的说明文本：整块抓出所有字符串值。
DESCRIPTION_BLOCK_PATTERN = re.compile(r"config_description\.update\(\s*\{(.*?)\}\s*\)", re.S)

# 中文译文表：键是英文源串。未列出的键视为「中文与英文相同」（缩写、数字等）。
CHINESE = {
    # 页签与任务
    "Scene Manager": "场景管理",
    "Scene Config": "场景配置",
    "Flow": "界面流转",
    "Drafts": "草稿确认",
    "Debug": "调试",
    "Scene Goto": "场景跳转",
    "Scene Explore": "场景探索",
    # 配置页
    "Scene": "场景",
    "New Scene": "新建场景",
    "Edit Scene": "编辑场景",
    "Delete Scene": "删除场景",
    "Save To assets": "保存到 assets",
    "Overwrite": "覆盖",
    "Copy": "复制",
    "OK": "确定",
    "Cancel": "取消",
    "Delete 「{name}」? This cannot be undone.": "将删除「{name}」，无法恢复。确定吗？",
    # 配置页卡片网格
    "Scenes": "场景",
    "Search by scene name...": "按场景名搜索…",
    "Delete": "删除",
    "Edit": "编辑",
    "Screenshot": "截图",
    "No scenes yet": "还没有场景",
    "Take a screenshot of the game, then mark up its features and interaction areas.": (
        "截一张游戏画面，然后标注识别特征与交互区域。"
    ),
    "Take Screenshot": "截图",
    "Capturing...": "正在截图…",
    # 分辨率一致性（截图准入与保存前检查）
    "Resolution Mismatch": "分辨率不一致",
    "Got It": "我知道了",
    "Use Current Frame": "以当前画面为基准",
    "Screenshot blocked: resolution mismatch.": "已禁止截图：画面分辨率与场景库不一致。",
    "Save blocked: resolution mismatch.": "已中止保存：存在分辨率不一致的场景。",
    "Cannot change the base resolution: the library is not empty.": (
        "无法修改基准分辨率：场景库中已有场景。"
    ),
    "Cannot rebase: no frame available.": "无法重建基准：取不到画面。",
    "Base resolution set to {size}.": "基准分辨率已设为 {size}。",
    "No scene matches the search.": "没有匹配的场景。",
    "Sync the edit library to assets/scenes": "把编辑区同步到 assets/scenes",
    "Some scenes could not be read: {list}": "有场景无法读取：{list}",
    "Screenshot failed: {e}": "截图失败：{e}",
    "Failed to capture: no frame. Check that a device is connected and that "
    "src/config.py matches the game window.": (
        "取不到画面。请确认已连接设备，且 src/config.py 的 windows/adb 配置能匹配到游戏窗口。"
    ),
    "Failed to create scene: {e}": "创建场景失败：{e}",
    "Created 「{name}」. Double-click the card to configure it.": (
        "已创建「{name}」。双击卡片开始配置。"
    ),
    "Delete 「{name}」 with its preview and templates? This cannot be undone.": (
        "将删除「{name}」及其预览图与模板，无法恢复。确定吗？"
    ),
    "Deleted 「{name}」 from the edit library.": "已从编辑区删除「{name}」。",
    "Legend": "图例",
    "Mandatory Feature": "必需特征",
    "Recognition Features": "识别特征",
    "Interaction Areas": "交互区域",
    "Required": "必需",
    "Optional": "可选",
    "Probe": "探测",
    "Template": "模板",
    "Text": "文本",
    "Scroll to zoom · Drag empty space to pan · Drag a box to move it, "
    "its edges to resize · Double-click to edit": (
        "滚轮缩放 · 拖动空白处平移 · 拖动框可移动，拖手柄改大小 · 双击编辑属性"
    ),
    "Optional Feature": "可选特征",
    "Interaction Area": "交互区域",
    # 弹窗
    "Scene Editor": "场景编辑",
    "Name": "名称",
    "Select": "选择",
    "Draw Feature Region": "框选特征区域",
    "Draw Interaction Region": "框选交互区域",
    "Capture Frame": "抓取画面",
    "Import Image": "导入图片",
    "Import failed: cannot read {name}": "导入失败：无法读取 {name}",
    "Import failed: unsupported image format": "导入失败：不支持的图片格式",
    "Discard": "放弃",
    "Next scene (→)": "下一个场景（→）",
    "Previous scene (←)": "上一个场景（←）",
    "Scene {id} cannot be read": "场景 {id} 无法读取",
    "Unsaved changes": "有未保存的改动",
    "Unsaved changes to 「{name}」 will be lost. Continue?": (
        "「{name}」的未保存改动将丢失。继续吗？"
    ),
    "Cancel": "取消",
    "Save": "保存",
    "Recognition Features": "识别特征",
    "Kind": "类型",
    "Required": "必需",
    "Rect (%)": "范围（%）",
    "Threshold": "阈值",
    "Interaction Areas": "交互区域",
    "Probe": "探索探测",
    "Target Scene": "目标场景",
    "Wait After Action": "操作后等待",
    "Template": "模板",
    "Text": "文本",
    "Yes": "是",
    "No": "否",
    "Allowed": "允许",
    "Off": "禁止",
    "Nothing added yet": "尚未添加",
    # 属性对话框
    "New Recognition Feature": "新增识别特征",
    "Edit Recognition Feature": "编辑识别特征",
    "New Interaction Region": "新增交互区域",
    "Edit Interaction Region": "编辑交互区域",
    "Recognition Method": "识别方式",
    "Template Match": "模板匹配",
    "Text (OCR)": "文本（OCR）",
    "Required (all must match)": "必需（全部命中才算识别成功）",
    "Optional (score only)": "可选（只加分）",
    "Threshold (0-1)": "阈值（0–1）",
    "Expected text (OCR)": "期望文本（OCR 用）",
    "(No edge)": "（不记录边）",
    "Allow Exploration Probe": "允许探索探测",
    "No (default, safer)": "否（默认，更安全）",
    "Yes (exploration acts on it)": "是（探索会对它执行动作）",
    "Exploration Action": "探索动作",
    "Single Click": "单击",
    "Double Click": "双击",
    "Long Press": "长按",
    "Swipe": "滑动",
    "Right Click": "右键单击",
    "Double Click Interval (s)": "双击间隔（秒）",
    "Long Press Duration (s)": "按住时长（秒）",
    "Swipe Distance (0-1)": "滑动距离（0–1）",
    "Swipe Direction": "滑动方向",
    "Up": "上",
    "Down": "下",
    "Left": "左",
    "Right": "右",
    "Wait After Action (s)": "操作后等待（秒）",
    # 流转页
    "Refresh": "刷新",
    "Preview Path": "预览路径",
    "Try One Step": "试走一步",
    "Start Exploration": "开始探索",
    "Export Library": "导出场景库",
    "Import Library": "导入场景库",
    "From": "从",
    "To": "到",
    "Click a node for details": "点节点查看详情",
    "Transitions": "跳转边",
    "From Scene": "来源场景",
    "Area": "区域",
    "Source": "来源类型",
    # 草稿页
    "Confirm Selected": "确认选中",
    "Confirm All": "全部确认",
    "Discard Selected": "丢弃选中",
    "Discard All": "全部丢弃",
    "Draft ID": "草稿 id",
    "Features": "特征数",
    "Areas": "区域数",
    "Drafts come from exploration and only join the library after confirmation. "
    "Confirming marks edges as confirmed; edges pointing to other drafts are cleared "
    "because edges may only target real scenes.": (
        "草稿是探索产物，确认后才并入正式场景库。确认时会把边升为「已确认」，"
        "指向其它草稿的边会被清空（边只能指向正式场景）。"
    ),
    # 调试页
    "(1) Live Recognition": "① 实时识别",
    "Identify Current Screen": "识别当前画面",
    "Start Live Refresh": "开始实时刷新",
    "Stop Live Refresh": "停止实时刷新",
    "Not identified yet": "尚未识别",
    "Similarity": "相似度",
    "Mandatory Hits": "必需命中",
    "Mandatory Total": "必需总数",
    "Optional Hits": "可选命中",
    "Missing Features": "缺失特征",
    "(2) Try One Step": "② 单步试走",
    "Activate And Observe": "点击并观察",
    "(3) Full Navigation": "③ 完整跳转",
    "Navigate": "执行跳转",
    # 任务
    "Navigate to the target scene along the scene flow graph": "沿场景流转图跳转到指定场景",
    "BFS-explore the screen, collect transition edges, and produce drafts": (
        "BFS 自动探索画面，采集跳转边并产出草稿"
    ),
    "Target scene id (e.g. scene-0001); ignored when called from code": (
        "要跳转到的场景 id（形如 scene-0001）；由代码调用时该项被忽略"
    ),
    "Overall timeout; abort when exceeded": "整体超时上限，超过则中止",
    "Hold the executor exclusively so other tasks cannot disturb the path": (
        "是否独占执行器，防止其他任务同时点击打乱路径"
    ),
    "Max number of new scenes before stopping": "新发现场景数上限，达到即停",
    "Max total seconds before stopping": "总时长上限（秒），达到即停",
    "Max activations per scene, retries included": "单个场景的操作次数上限（含重试）",
    # 配置键同时是界面标签（框架对 `key` 也调 `tr()`），因此也要有中文译文。
    "Target Scene": "目标场景",
    "Timeout Seconds": "超时时间（秒）",
    "Use Exclusive": "独占执行器",
    "Max New Scenes": "新场景数上限",
    "Max Seconds": "总时长上限（秒）",
    "Max Clicks Per Scene": "单场景操作上限",
}

HEADER = (
    "#\n"
    'msgid ""\n'
    'msgstr ""\n'
    '"Project-Id-Version: 1.0\\n"\n'
    '"Report-Msgid-Bugs-To: you@example.com\\n"\n'
    '"Last-Translator: you@example.com\\n"\n'
    '"Language-Team: {team}\\n"\n'
    '"MIME-Version: 1.0\\n"\n'
    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Content-Transfer-Encoding: 8bit\\n"\n'
    '"Language: {lang}\\n"\n'
)


def collect_texts():
    """从能力包源码里收集界面文案（全部是 `tr()` 的英文实参）。

    Returns：
        去重后的英文源串集合。
    """
    texts = set()
    for path in CAPABILITY.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for match in TRANSLATE_CALL.finditer(source):
            texts.add(match.group(1))
        for match in TRANSLATE_MULTILINE.finditer(source):
            parts = STRING_LITERAL.findall(match.group(1))
            if len(parts) > 1:
                texts.add("".join(parts))
        for match in TASK_DESCRIPTION.finditer(source):
            texts.add(match.group(1))
        for match in MODE_LABEL_TABLE.finditer(source):
            texts.add(match.group(1))
        for block in LABEL_TABLE_BLOCK.finditer(source):
            body = block.group(2)
            for match in LABEL_PAIR_TABLE.finditer(body):
                texts.add(match.group(1))
            for match in PARAM_LABEL_TABLE.finditer(body):
                texts.add(match.group(1))
        # 配置键与说明只在 tasks.py 里。
        if path.name == "tasks.py":
            for match in CONFIG_KEY_PATTERN.finditer(source):
                texts.add(match.group(1))
            for match in CONFIG_GET_PATTERN.finditer(source):
                texts.add(match.group(1))
            for block in DESCRIPTION_BLOCK_PATTERN.finditer(source):
                for item in STRING_LITERAL.findall(block.group(1)):
                    texts.add(item)
    return {text for text in texts if text and not text.startswith(" ")}


def escape(text):
    """转义 po 字符串中的特殊字符。

    Args：
        text：原文本。

    Returns：
        可直接写进 msgid/msgstr 的文本。
    """
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def write_po(path, mapping, lang, team):
    """写出一个 po 文件。

    Args：
        path：目标路径。
        mapping：`{msgid: msgstr}` 字典。
        lang：语言标识。
        team：Language-Team 头部取值。
    """
    chunks = [HEADER.format(team=team, lang=lang)]
    for key in sorted(mapping):
        chunks.append('msgid "%s"\nmsgstr "%s"\n' % (escape(key), escape(mapping[key])))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(chunks), encoding="utf-8")


def main():
    """生成中英两份 catalog，并报告缺失中文译文的条目。"""
    texts = collect_texts()
    # 英文即源语言：msgstr 与 msgid 相同。
    en_map = {key: key for key in texts}
    zh_map = {key: CHINESE.get(key, key) for key in texts}

    base = CAPABILITY / "i18n"
    write_po(base / "zh_CN" / "LC_MESSAGES" / "ok_ext.po", zh_map, "zh_CN", "Chinese")
    write_po(base / "en_US" / "LC_MESSAGES" / "ok_ext.po", en_map, "en_US", "English")

    untranslated = sorted(key for key in texts if re.search(r"[A-Za-z]", key) and zh_map[key] == key)
    print(
        json.dumps(
            {"zh": len(zh_map), "en": len(en_map), "未翻译": untranslated},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
