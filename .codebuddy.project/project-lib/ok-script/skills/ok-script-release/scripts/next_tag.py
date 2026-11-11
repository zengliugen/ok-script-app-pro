#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计算下一个发布版本号 tag。

支持三条互相独立的号段：

- ``release``：稳定版，在最新稳定版上 patch 加一，如 ``v3.3.54`` → ``v3.3.55``；
- ``beta`` / ``alpha``：预发布，同一条未发布线继续加后缀，否则从下一个 patch 的 ``.1`` 起。

预发布的边界规则：基线版本已经发过正式版时视为已关闭，不再加后缀，改为从下一个 patch 起算。
例如稳定版已有 ``v3.3.55``，``v3.3.55-beta.3`` 属已关闭，新 beta 应为 ``v3.3.56-beta.1``。

无稳定版 tag 时的兜底：仓库尚未发过正式版、但同渠道已有预发布时，以该预发布为基线继续递增
（``v0.0.1-beta.1`` → ``v0.0.1-beta.2``）。``release`` 渠道不适用该兜底——稳定版号段必须有
稳定版基线，凭空指定一个首版号属于人工决策，脚本不做推断。

概览：
    ``python next_tag.py <channel>`` 默认读取**本仓库自有** tag 作为基线——
    本地与 origin 的并集，剔除指向 ``master``（上游镜像）的上游 tag。
    ``--tag`` 显式给定基线，供排障与离线使用。

退出码：
    0 成功，向标准输出打印算出的 tag
    1 无法计算（无可用基线、远端查询失败等）
    2 参数错误（由 argparse 抛出）
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

# 稳定版格式：v主.次.补丁，不允许前导零以外的杂字符。
STABLE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
# 预发布格式：v主.次.补丁-(alpha|beta).序号。
PRERELEASE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)-(alpha|beta)\.(\d+)$")


def init_stdout():
    """Windows 下统一以 UTF-8 输出，避免中文乱码。"""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


@dataclass(frozen=True, order=True)
class Version:
    """版本号三元组，字段顺序即比较顺序。"""

    major: int
    minor: int
    patch: int

    def next_patch(self) -> "Version":
        """返回 patch 加一后的版本。"""
        return Version(self.major, self.minor, self.patch + 1)

    def tag(self) -> str:
        """返回稳定版 tag 字符串。"""
        return "v%d.%d.%d" % (self.major, self.minor, self.patch)


@dataclass(frozen=True, order=True)
class Prerelease:
    """预发布条目，先比基线版本再比序号。"""

    version: Version
    number: int


def repository_tags() -> list[str]:
    """读取本仓库**自有**的 v* tag（本地与 origin 的并集，剔除上游镜像带来的 tag）。

    为什么不能用 ``git tag --list v*``：本仓库 fork 自上游，本地 tag 里混着大量
    ``ok-oldking`` 的上游版本号（实测 46 个，且会被 IDE 的自动 fetch 反复拉回）。
    直接读本地会把发布号带到上游序列上——实测算出 ``v1.1.4-alpha.1`` 而非 ``v0.0.1-alpha.2``。

    为什么不能只读 origin：tag 打好但尚未推送时 origin 上没有它，
    此时算下一个号会丢掉这条基线，把 alpha.2 又算成 alpha.1。

    区分办法是**提交归属**，不是 tag 名，也不是 tagger：

    - 上游 tag 指向的提交必然在 ``master`` 上（master 是纯上游镜像，只随上游前移）；
    - 本仓库自有的 tag 打在 ``dev`` 的提交上，``master`` 不含它。

    判据因此是「tag 指向的提交是否为 master 的祖先」。它不依赖 tag 命名、不依赖
    tagger 身份，也不受外部工具把上游 tag 拉回本地的影响——上游 tag 再怎么被拉回，
    它们的提交仍在 master 上，照样被剔除。

    ``refs/remotes/origin/*`` 上的 tag 单独补一遍，用来覆盖「远端有、本地还没拉」的
    自有 tag（tag 小幅领先于本地 clone 的情形）。

    Returns：
        自有 tag 名列表；master 不可用（如浅克隆）时退化为只读 origin 远端 tag。

    Raises：
        subprocess.CalledProcessError：git 命令失败。
    """
    result = subprocess.run(
        ["git", "tag", "--list", "v*"],
        check=True,
        capture_output=True,
        text=True,
    )
    local_tags = result.stdout.splitlines()

    owned = [tag for tag in local_tags if _is_owned_tag(tag)]
    owned.extend(_origin_only_tags(owned))
    return owned


def _is_owned_tag(tag: str) -> bool:
    """判断某个本地 tag 是否为本仓库自有（指向的提交不在 master 上游镜像上）。

    Args：
        tag: tag 名。

    Returns：
        True 表示自有；master 查询失败时返回 True（宁可多算一条，也不静默丢基线）。
    """
    commit = subprocess.run(
        ["git", "rev-list", "-n", "1", tag],
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0 or not commit.stdout.strip():
        return True
    sha = commit.stdout.strip()
    # master 是纯上游镜像：上游 tag 指向的提交必然在它上面。
    on_master = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "master"],
        capture_output=True,
        text=True,
    )
    if on_master.returncode not in (0, 1):
        return True
    return on_master.returncode == 1


def _origin_only_tags(local_owned: list[str]) -> list[str]:
    """取出「远端有、本地没有」的自有 tag，用于覆盖本地 clone 略旧的情形。

    Args：
        local_owned: 已判定的本地自有 tag。

    Returns：
        仅存在于 origin 的自有 tag 名列表；origin 不可达时返回空列表。
    """
    result = subprocess.run(
        ["git", "ls-remote", "--refs", "--tags", "origin", "refs/tags/v*"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    known = set(local_owned)
    remote_names = [
        ref.removeprefix("refs/tags/")
        for _, ref in (line.split("\t", 1) for line in result.stdout.splitlines() if "\t" in line)
    ]
    # origin 上的 tag 全部由本仓库推送，不存在上游 tag，直接取差集。
    return [name for name in remote_names if name not in known]


def parse_tags(tags: list[str]) -> tuple[list[Version], dict[str, list[Prerelease]]]:
    """把 tag 字符串列表分为稳定版与预发布两组，忽略不符合格式的项。

    Args：
        tags：tag 字符串列表，可含重复与无关项。

    Returns：
        二元组：稳定版列表与按渠道分组的预发布字典。
    """
    stable: list[Version] = []
    prereleases: dict[str, list[Prerelease]] = {"alpha": [], "beta": []}
    for tag in tags:
        if match := STABLE_RE.fullmatch(tag.strip()):
            stable.append(Version(*(int(value) for value in match.groups())))
            continue
        if match := PRERELEASE_RE.fullmatch(tag.strip()):
            major, minor, patch, channel, number = match.groups()
            prereleases[channel].append(Prerelease(Version(int(major), int(minor), int(patch)), int(number)))
    return stable, prereleases


def next_tag(channel: str, tags: list[str]) -> str:
    """按渠道与已有 tag 计算下一个版本号。

    Args：
        channel：发布渠道，取 release / beta / alpha 之一。
        tags：已有 tag 字符串列表，可含不符合格式的无关项。

    Returns：
        下一个 tag 字符串，形如 v3.3.55 或 v3.3.56-beta.1。

    Raises：
        ValueError：既没有稳定版 tag，也没有该渠道的预发布可作基线。
    """
    stable, prereleases = parse_tags(tags)

    if not stable:
        # 尚无正式版：允许用同渠道最高预发布当基线继续递增（v0.0.1-beta.1 → -beta.2）。
        # 只放宽预发布；release 渠道仍要求稳定版基线——首版号是人工决策，
        # 脚本凭空推一个 v0.0.1 会与真实意图不符。
        # prereleases 只含 alpha/beta 两个键，release 渠道必须先判渠道再取键，
        # 否则 prereleases["release"] 抛 KeyError 而不是给出可读的拒绝理由。
        if channel != "release":
            channel_tags = prereleases[channel]
            if channel_tags:
                latest_prerelease = max(channel_tags)
                return "%s-%s.%d" % (
                    latest_prerelease.version.tag(),
                    channel,
                    latest_prerelease.number + 1,
                )
        raise ValueError(
            "仓库中没有任何 vMAJOR.MINOR.PATCH 稳定版 tag，无法计算「%s」渠道的版本号。\n"
            "首个正式版号需人工指定（如 v0.1.0），脚本不做无稳定版基线的推断。" % channel
        )

    latest_stable = max(stable)
    if channel == "release":
        return latest_stable.next_patch().tag()

    channel_tags = prereleases[channel]
    if channel_tags:
        latest_prerelease = max(channel_tags)
        # 只有基线版本仍高于最新稳定版时，才在同一条线上继续加后缀；
        # 否则该基线已经发过正式版，预发布线已关闭。
        if latest_prerelease.version > latest_stable:
            return "%s-%s.%d" % (
                latest_prerelease.version.tag(),
                channel,
                latest_prerelease.number + 1,
            )

    return "%s-%s.1" % (latest_stable.next_patch().tag(), channel)


def main() -> int:
    """解析参数、计算并打印结果。"""
    init_stdout()
    parser = argparse.ArgumentParser(description="计算下一个发布版本号 tag")
    parser.add_argument("channel", choices=("release", "beta", "alpha"), help="发布渠道")
    parser.add_argument(
        "--tag",
        action="append",
        dest="tags",
        help="用显式给定的已有 tag 代替读取仓库，可重复传入（排障或离线时使用）",
    )
    args = parser.parse_args()

    try:
        tags = list(args.tags) if args.tags is not None else repository_tags()
        print(next_tag(args.channel, tags))
    except subprocess.CalledProcessError as error:
        print(
            "读取 tag 失败（%s）。版本基线取本仓库自有 tag（已剔除上游镜像带来的 tag），\n"
            "查询失败时**不**回退到全量本地 tag——那会把上游版本号算进发布号。\n"
            "排查后再试，或用 --tag 显式给定基线。" % (error.stderr or error),
            file=sys.stderr,
        )
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
