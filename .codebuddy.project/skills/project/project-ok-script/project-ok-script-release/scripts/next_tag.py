#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计算下一个发布版本号 tag。

支持三条互相独立的号段：

- ``release``：稳定版，在最新稳定版上 patch 加一，如 ``v3.3.54`` → ``v3.3.55``；
- ``beta`` / ``alpha``：预发布，同一条未发布线继续加后缀，否则从下一个 patch 的 ``.1`` 起。

预发布的边界规则：基线版本已经发过正式版时视为已关闭，不再加后缀，改为从下一个 patch 起算。
例如稳定版已有 ``v3.3.55``，``v3.3.55-beta.3`` 属已关闭，新 beta 应为 ``v3.3.56-beta.1``。

退出码：
    0 成功，向标准输出打印算出的 tag
    1 无法计算（没有任何稳定版 tag、远端查询失败等）
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
    """读取本地仓库的 v* tag。

    Raises：
        subprocess.CalledProcessError：git 命令失败。
    """
    result = subprocess.run(
        ["git", "tag", "--list", "v*"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def remote_tags(remote: str) -> list[str]:
    """读取指定远端的 v* tag。

    Raises：
        subprocess.CalledProcessError：git 命令失败（远端不可达、无权限等）。
    """
    result = subprocess.run(
        ["git", "ls-remote", "--refs", "--tags", remote, "refs/tags/v*"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [ref.removeprefix("refs/tags/") for _, ref in (line.split("\t", 1) for line in result.stdout.splitlines())]


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
        ValueError：没有任何稳定版 tag，无法确定基线。
    """
    stable, prereleases = parse_tags(tags)
    if not stable:
        raise ValueError("仓库中没有任何 vMAJOR.MINOR.PATCH 稳定版 tag，无法计算版本号。")

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
        help="用显式给定的已有 tag 代替读取当前仓库，可重复传入",
    )
    parser.add_argument(
        "--remote",
        help="同时纳入该远端上已发布的 tag，例如 origin",
    )
    args = parser.parse_args()

    try:
        tags = args.tags if args.tags is not None else repository_tags()
        if args.remote:
            tags.extend(remote_tags(args.remote))
        print(next_tag(args.channel, tags))
    except (subprocess.CalledProcessError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
