#!/usr/bin/env python3
"""Deterministic USB Android helpers for the Xiaohongshu publishing skill."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import re
from datetime import datetime
from pathlib import Path

# Windows 默认用 GBK 编码，手机返回的中文和 emoji 会崩。
# 强制 stdout/stderr 用 UTF-8，后端 subprocess 也用 UTF-8 读。
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


XHS_PACKAGE = "com.xingin.xhs"
DEFAULT_ANDROID_USER = "0"
ADB_IME = "com.android.adbkeyboard/.AdbIME"
CALIBRATION_SCHEMA = 1


def adb_path() -> str:
    candidates = [
        os.environ.get("XHS_ADB"),
        shutil.which("adb"),
        str(Path.home() / ".local/share/xhs-publishing/platform-tools/adb"),
        str(Path.home() / ".local/share/xhs-publishing/platform-tools/adb.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("adb not found. Run scripts/setup_android.py first.")


def run_adb(
    serial: str | None,
    *args: str,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = [adb_path()]
    if serial:
        command += ["-s", serial]
    command += list(args)
    return subprocess.run(
        command,
        check=check,
        text=True,
        capture_output=capture,
        encoding="utf-8",
        # A stuck uiautomator/USB call must not hold the whole sync task until
        # its outer 900-second process timeout. dump_ui() will retry transient
        # failures and surface a readable error when the device is genuinely
        # unavailable.
        timeout=45,
    )


def connected_devices() -> list[dict[str, str]]:
    result = run_adb(None, "devices", "-l").stdout.splitlines()[1:]
    devices: list[dict[str, str]] = []
    for line in result:
        parts = line.split()
        if len(parts) < 2:
            continue
        record = {"serial": parts[0], "state": parts[1]}
        for token in parts[2:]:
            if ":" in token:
                key, value = token.split(":", 1)
                record[key] = value
        devices.append(record)
    return devices


def require_serial(serial: str) -> None:
    states = {item["serial"]: item["state"] for item in connected_devices()}
    if serial not in states:
        raise SystemExit(f"Device is not connected: {serial}")
    if states[serial] != "device":
        raise SystemExit(f"Device is not authorized/ready: {serial} ({states[serial]})")


def getprop(serial: str, name: str) -> str:
    return run_adb(serial, "shell", "getprop", name).stdout.strip()


def current_ime(serial: str) -> str:
    return run_adb(
        serial, "shell", "settings", "get", "secure", "default_input_method"
    ).stdout.strip()


def dump_ui(serial: str) -> ET.Element:
    remote = "/sdcard/xhs-skill-window.xml"
    # uiautomator dump 偶尔会返回 255（服务忙或正在动画），重试 3 次。
    last_error = None
    for attempt in range(3):
        try:
            run_adb(serial, "shell", "uiautomator", "dump", remote)
            xml_text = run_adb(serial, "exec-out", "cat", remote).stdout
            if xml_text and xml_text.strip():
                return ET.fromstring(xml_text)
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"uiautomator dump failed after 3 retries: {last_error}")


def center(bounds: str) -> tuple[int, int]:
    cleaned = bounds.replace("][", ",").strip("[]")
    x1, y1, x2, y2 = (int(value) for value in cleaned.split(","))
    return (x1 + x2) // 2, (y1 + y2) // 2


def find_text_field(root: ET.Element, placeholder: str) -> tuple[int, int]:
    preferred_ids = {
        "添加标题": "com.xingin.xhs:id/b5i",
        "添加正文": "com.xingin.xhs:id/b4d",
    }
    for node in root.iter("node"):
        is_edit_text = node.attrib.get("class", "").endswith("EditText")
        if is_edit_text and (
            node.attrib.get("text") == placeholder
            or node.attrib.get("text", "").startswith(placeholder)
            or node.attrib.get("hint") == placeholder
            or node.attrib.get("hint", "").startswith(placeholder)
            or node.attrib.get("resource-id") == preferred_ids.get(placeholder)
        ):
            return center(node.attrib["bounds"])
    raise SystemExit(
        f"Cannot find {placeholder!r}. Navigate to the Xiaohongshu compose page first."
    )


def find_node(
    root: ET.Element,
    *,
    texts: tuple[str, ...] = (),
    descriptions: tuple[str, ...] = (),
    resource_ids: tuple[str, ...] = (),
) -> ET.Element | None:
    for node in root.iter("node"):
        text = node.attrib.get("text", "")
        description = node.attrib.get("content-desc", "")
        resource_id = node.attrib.get("resource-id", "")
        if texts and any(text == item or text.startswith(item) for item in texts):
            return node
        if descriptions and any(description == item or description.startswith(item) for item in descriptions):
            return node
        if resource_ids and resource_id in resource_ids:
            return node
    return None


def _scrollable_node(root: ET.Element) -> ET.Element | None:
    """Find the tallest scrollable container, which is the list we want to page."""
    best: ET.Element | None = None
    best_height = 0
    for node in root.iter("node"):
        if node.attrib.get("scrollable") != "true":
            continue
        _, height = _bounds_size(node)
        if height > best_height:
            best, best_height = node, height
    return best


def _profile_notes_container(root: ET.Element) -> ET.Element | None:
    """Find the vertical notes list on a profile page.

    A profile screen can contain a full-screen ViewPager as well as the
    actual notes RecyclerView.  Swiping the pager does not advance the note
    grid, so prefer the largest scrollable RecyclerView and fall back to the
    generic tallest container for older app versions.
    """
    recycler_views: list[tuple[int, ET.Element]] = []
    for node in root.iter("node"):
        if node.attrib.get("scrollable") != "true":
            continue
        class_name = node.attrib.get("class", "")
        if "RecyclerView" not in class_name:
            continue
        try:
            _, height = _bounds_size(node)
        except (KeyError, ValueError):
            continue
        recycler_views.append((height, node))
    if recycler_views:
        return max(recycler_views, key=lambda item: item[0])[1]
    return _scrollable_node(root)


def _swipe_scrollable_container(
    serial: str,
    root: ET.Element,
    *,
    direction: str,
    duration_ms: int = 650,
) -> bool:
    """Swipe inside a scrollable container using its actual screen bounds."""
    container = _profile_notes_container(root)
    if container is None:
        return False
    try:
        left, top, right, bottom = (
            int(value)
            for value in container.attrib["bounds"].replace("][", ",").strip("[]").split(",")
        )
    except (KeyError, ValueError):
        return False
    width = max(1, right - left)
    height = max(1, bottom - top)
    mid_x = left + width // 2
    # Keep the gesture away from the status/navigation bars while still
    # traversing most of the list in one swipe.
    margin = max(24, height // 10)
    if direction == "up":
        start_y, end_y = bottom - margin, top + margin
    else:
        start_y, end_y = top + margin, bottom - margin
    run_adb(
        serial, "shell", "input", "swipe",
        str(mid_x), str(start_y), str(mid_x), str(end_y), str(duration_ms),
        check=False,
    )
    return True


def scroll_to_find_node(
    serial: str,
    *,
    texts: tuple[str, ...] = (),
    descriptions: tuple[str, ...] = (),
    resource_ids: tuple[str, ...] = (),
    max_scrolls: int = 12,
) -> ET.Element | None:
    """Look for a node, scrolling the list when it is not on screen yet.

    uiautomator only dumps rendered rows, so an entry further down a
    RecyclerView simply is not in the hierarchy. Without this, a target that
    is visibly "just below the fold" reads as missing.

    Swipe distance is derived from the scrollable container's own bounds
    instead of hardcoded pixels, so it works across screen sizes.
    """
    root = dump_ui(serial)
    found = find_node(root, texts=texts, descriptions=descriptions, resource_ids=resource_ids)
    if found is not None:
        return found

    previous_signature: str | None = None
    for _ in range(max_scrolls):
        container = _scrollable_node(root)
        if container is None:
            return None
        cleaned = container.attrib["bounds"].replace("][", ",").strip("[]")
        left, top, right, bottom = (int(value) for value in cleaned.split(","))
        mid_x = (left + right) // 2
        # Swipe across ~60% of the container so rows are not skipped entirely.
        span = (bottom - top) * 6 // 10
        start_y = top + (bottom - top) * 8 // 10
        run_adb(
            serial, "shell", "input", "swipe",
            str(mid_x), str(start_y), str(mid_x), str(max(top + 10, start_y - span)), "600",
            check=False,
        )
        time.sleep(1.0)

        root = dump_ui(serial)
        found = find_node(root, texts=texts, descriptions=descriptions, resource_ids=resource_ids)
        if found is not None:
            return found

        # Stop early when the list no longer changes — already at the bottom.
        signature = "|".join(
            node.attrib.get("text", "") for node in root.iter("node") if node.attrib.get("text")
        )
        if signature == previous_signature:
            return None
        previous_signature = signature
    return None


def wait_node(
    serial: str,
    *,
    texts: tuple[str, ...] = (),
    descriptions: tuple[str, ...] = (),
    resource_ids: tuple[str, ...] = (),
    timeout: float = 15,
) -> ET.Element:
    deadline = time.time() + timeout
    while time.time() < deadline:
        node = find_node(
            dump_ui(serial),
            texts=texts,
            descriptions=descriptions,
            resource_ids=resource_ids,
        )
        if node is not None:
            return node
        time.sleep(0.8)
    expected = texts or descriptions or resource_ids
    raise SystemExit(f"Cannot find required Xiaohongshu control: {expected}")


def tap_node(serial: str, node: ET.Element) -> None:
    point = center(node.attrib["bounds"])
    run_adb(serial, "shell", "input", "tap", *map(str, point))


def tap_thumbnail_selector(serial: str, node: ET.Element) -> None:
    """Tap the multi-select circle instead of opening the image preview."""
    cleaned = node.attrib["bounds"].replace("][", ",").strip("[]")
    x1, y1, x2, y2 = (int(value) for value in cleaned.split(","))
    inset = max(32, min(58, (x2 - x1) // 6, (y2 - y1) // 6))
    run_adb(serial, "shell", "input", "tap", str(x2 - inset), str(y1 + inset))


def _bounds_size(node: ET.Element) -> tuple[int, int]:
    cleaned = node.attrib["bounds"].replace("][", ",").strip("[]")
    x1, y1, x2, y2 = (int(value) for value in cleaned.split(","))
    return x2 - x1, y2 - y1


def product_search_control(root: ET.Element) -> ET.Element | None:
    """Find the product search field/icon without assuming a screen width."""
    for node in root.iter("node"):
        if not node.attrib.get("class", "").endswith("EditText"):
            continue
        label = "".join(
            node.attrib.get(attribute, "")
            for attribute in ("text", "hint", "content-desc")
        )
        if "搜索" in label:
            return node

    category_bounds = []
    for node in root.iter("node"):
        if node.attrib.get("text", "") not in {"普通商品", "店内爆品", "最新上架", "热门搜索"}:
            continue
        bounds = node.attrib.get("bounds")
        if bounds:
            category_bounds.append(
                tuple(int(value) for value in bounds.replace("][", ",").strip("[]").split(","))
            )
    if not category_bounds:
        return None

    row_top = min(bounds[1] for bounds in category_bounds)
    row_bottom = max(bounds[3] for bounds in category_bounds)
    row_right = max(bounds[2] for bounds in category_bounds)
    candidates = []
    for node in root.iter("node"):
        if node.attrib.get("clickable") != "true":
            continue
        if not node.attrib.get("class", "").endswith("ImageView"):
            continue
        bounds_text = node.attrib.get("bounds")
        if not bounds_text:
            continue
        bounds = tuple(
            int(value) for value in bounds_text.replace("][", ",").strip("[]").split(",")
        )
        center_y = (bounds[1] + bounds[3]) // 2
        if bounds[0] >= row_right and row_top <= center_y <= row_bottom:
            candidates.append((bounds[0], node))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def product_result_node(root: ET.Element, keyword: str) -> ET.Element | None:
    """Find a product result title while excluding the search input itself."""
    candidates = [
        node for node in root.iter("node")
        if node.attrib.get("class", "").endswith("TextView")
        and keyword in node.attrib.get("text", "").strip()
    ]
    exact = next(
        (node for node in candidates if node.attrib.get("text", "").strip() == keyword),
        None,
    )
    return exact or (candidates[0] if candidates else None)


def scroll_to_find_product_result(
    serial: str,
    keyword: str,
    *,
    max_scrolls: int = 12,
) -> tuple[ET.Element | None, ET.Element]:
    """Find a product title across rendered result pages.

    Product search results are commonly virtualized, so a single UI dump can
    miss an otherwise valid match. Keep the product-specific predicate here so
    the search EditText itself is never mistaken for a result row.
    """
    root = dump_ui(serial)
    found = product_result_node(root, keyword)
    if found is not None:
        return found, root

    previous_signature: str | None = None
    for _ in range(max_scrolls):
        container = _scrollable_node(root)
        if container is None:
            return None, root
        cleaned = container.attrib["bounds"].replace("][", ",").strip("[]")
        left, top, right, bottom = (int(value) for value in cleaned.split(","))
        mid_x = (left + right) // 2
        span = (bottom - top) * 6 // 10
        start_y = top + (bottom - top) * 8 // 10
        run_adb(
            serial, "shell", "input", "swipe",
            str(mid_x), str(start_y), str(mid_x), str(max(top + 10, start_y - span)), "600",
            check=False,
        )
        time.sleep(1.0)
        root = dump_ui(serial)
        found = product_result_node(root, keyword)
        if found is not None:
            return found, root

        signature = "|".join(
            node.attrib.get("text", "")
            for node in root.iter("node")
            if node.attrib.get("text")
        )
        if signature == previous_signature:
            return None, root
        previous_signature = signature
    return None, root


def clickable_ancestor(root: ET.Element, node: ET.Element) -> ET.Element | None:
    """Return the nearest clickable parent from the node's actual XML branch."""
    parents = {child: parent for parent in root.iter() for child in parent}
    current = parents.get(node)
    while current is not None:
        if current.attrib.get("clickable") == "true":
            return current
        current = parents.get(current)
    return None


def topic_candidate(root: ET.Element, topic: str) -> ET.Element | None:
    """Find an exact Xiaohongshu topic suggestion, excluding editor text."""
    return next((
        node for node in root.iter("node")
        if node.attrib.get("class", "").endswith("TextView")
        and node.attrib.get("text", "").strip() == topic
    ), None)


def wait_topic_candidate(
    serial: str,
    topic: str,
    *,
    timeout: float = 5,
) -> tuple[ET.Element | None, ET.Element]:
    deadline = time.time() + timeout
    root = dump_ui(serial)
    while True:
        candidate = topic_candidate(root, topic)
        if candidate is not None or time.time() >= deadline:
            return candidate, root
        time.sleep(0.5)
        root = dump_ui(serial)


def topics_from_file(path: str | None) -> list[str]:
    if not path:
        return []
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("topics-file must contain a JSON list")
    topics = []
    for item in raw:
        topic = str(item).strip().lstrip("#")
        if topic and topic not in topics:
            topics.append(topic)
    return topics[:10]


def selectable_thumbnails(root: ET.Element) -> list[ET.Element]:
    """Find gallery tiles across stable and resource-obfuscated XHS builds.

    Results are sorted by on-screen position (top-to-bottom, left-to-right)
    so that tapping them in order matches the visual order the user sees.
    Without this, XML traversal order could differ from layout order and
    images would be selected in the wrong sequence.
    """
    def _position_key(node: ET.Element) -> tuple[int, int]:
        bounds = node.attrib.get("bounds", "[0,0][0,0]")
        cleaned = bounds.replace("][", ",").strip("[]")
        parts = cleaned.split(",")
        top = int(parts[1]) if len(parts) >= 4 else 0
        left = int(parts[0]) if len(parts) >= 4 else 0
        # 行高按 300 像素分组，同一行的图左右排序
        return (top // 300, left)

    stable = [
        node for node in root.iter("node")
        if node.attrib.get("resource-id") == "com.xingin.xhs:id/fk7"
        and node.attrib.get("clickable") == "true"
    ]
    if stable:
        return sorted(stable, key=_position_key)
    candidates: list[ET.Element] = []
    for node in root.iter("node"):
        if node.attrib.get("clickable") != "true":
            continue
        if not node.attrib.get("class", "").endswith("ImageView"):
            continue
        width, height = _bounds_size(node)
        # 三列相册在 720px 宽设备上单格只有 236px。旧阈值 250 会把真实
        # 缩略图全部排除，最终误报 0 张；180 仍能排除顶部按钮和 100px 选择圈。
        if width >= 180 and height >= 180 and abs(width - height) <= 40:
            candidates.append(node)
    return sorted(candidates, key=_position_key)


def open_dedicated_album(serial: str) -> ET.Element:
    """Open CodexXHS using semantic controls, including newer bottom sheets."""
    root = dump_ui(serial)

    # 新版小红书点 + 号后先出底部菜单（相册/商品/拍视频等），要选「相册」才进选图页面。
    album_entry = find_node(root, texts=("相册",))
    if album_entry is not None:
        tap_node(serial, album_entry)
        time.sleep(1.5)
        root = dump_ui(serial)

    choose_from_album = find_node(root, texts=("从相册选择", "从相册中选择"))
    if choose_from_album is not None:
        tap_node(serial, choose_from_album)
        time.sleep(1.5)

    root = dump_ui(serial)
    photos_tab = find_node(root, texts=("照片",))
    if photos_tab is not None:
        tap_node(serial, photos_tab)
        time.sleep(0.8)

    # 相册选择器可能要等页面加载完才出现，重试 3 次。
    album_selector = None
    for attempt in range(3):
        root = dump_ui(serial)
        album_selector = find_node(root, resource_ids=("com.xingin.xhs:id/jo",))
        if album_selector is None:
            album_selector = find_node(root, texts=("全部", "最近", "CodexXHS"))
        if album_selector is not None:
            break
        time.sleep(1.5)
    if album_selector is None:
        raise SystemExit(
            "Cannot open the Xiaohongshu album selector; device calibration is required"
        )
    tap_node(serial, album_selector)
    time.sleep(0.8)
    # 相册列表是可滚动列表，CodexXHS 常常在屏幕外。此前只 dump 一次就判定「找不到」，
    # 而 uiautomator 只会导出已渲染的行，于是明明在下面的相册被当成不存在。
    codex_album = scroll_to_find_node(serial, texts=("CodexXHS",))
    if codex_album is None:
        visible = sorted({
            node.attrib.get("text", "").strip()
            for node in dump_ui(serial).iter("node")
            if node.attrib.get("text", "").strip()
        })
        raise SystemExit(
            "Cannot find the dedicated CodexXHS image album. "
            f"已滚动查找仍未命中。当前可见文本：{visible[:40]}"
        )
    tap_node(serial, codex_album)
    time.sleep(1)
    root = dump_ui(serial)
    # XHS 9.40 may open an album in single-photo mode. Enable multi-select
    # before tapping thumbnails, otherwise the first tap immediately enters
    # the editor and the remaining images are silently skipped.
    multi_select = find_node(
        root,
        texts=("多选", "选择多张"),
        descriptions=("多选", "选择多张"),
    )
    if multi_select is not None:
        tap_node(serial, multi_select)
        time.sleep(0.8)
        root = dump_ui(serial)
    return root


def enter_gallery(serial: str, android_user: str = DEFAULT_ANDROID_USER) -> ET.Element:
    """Start a note and stop safely in the dedicated image album."""
    run_adb(serial, "shell", "am", "force-stop", "--user", android_user, XHS_PACKAGE)
    command_open(type("Args", (), {"serial": serial, "android_user": android_user})())
    time.sleep(2)
    entry = wait_node(
        serial,
        texts=("去发布",),
        descriptions=("发布",),
        resource_ids=("com.xingin.xhs:id/b8c",),
    )
    tap_node(serial, entry)
    time.sleep(1.5)
    root = dump_ui(serial)
    unfinished = find_node(root, texts=("你有未完成的笔记",))
    if unfinished is not None:
        abandon = find_node(root, texts=("放弃",))
        if abandon is None:
            raise SystemExit("An unfinished-note dialog is blocking publication")
        tap_node(serial, abandon)
        time.sleep(1)
        entry = wait_node(serial, descriptions=("发布",), texts=("去发布",))
        tap_node(serial, entry)
        time.sleep(1.5)
    return open_dedicated_album(serial)


def fill_title_body(serial: str, title: str, body: str) -> None:
    original_ime = current_ime(serial)
    try:
        root = dump_ui(serial)
        title_point = find_text_field(root, "添加标题")
        # 输入法可能没装或启用失败，尝试重装。
        try:
            run_adb(serial, "shell", "ime", "enable", ADB_IME)
        except subprocess.CalledProcessError:
            _log("[输入法] enable 失败，尝试重装 ADBKeyboard")
            apk = Path(__file__).resolve().parent.parent / "assets" / "ADBKeyboard.apk"
            run_adb(serial, "install", "-r", str(apk), capture=False)
            run_adb(serial, "shell", "pm", "enable", "com.android.adbkeyboard", check=False)
            run_adb(serial, "shell", "ime", "enable", ADB_IME)
        run_adb(serial, "shell", "ime", "set", ADB_IME)
        run_adb(serial, "shell", "input", "tap", *map(str, title_point))
        time.sleep(0.5)
        run_adb(serial, "shell", "input", "keyevent", "123")
        run_adb(serial, "shell", "input", "keyevent", *(["67"] * 100))
        broadcast_unicode(serial, title)
        time.sleep(0.8)
        # The compose page moves after the keyboard opens. Re-read the UI
        # instead of reusing the pre-keyboard body coordinate.
        body_point = find_text_field(dump_ui(serial), "添加正文")
        run_adb(serial, "shell", "input", "tap", *map(str, body_point))
        time.sleep(0.5)
        run_adb(serial, "shell", "input", "keyevent", "123")
        run_adb(serial, "shell", "input", "keyevent", *(["67"] * 200))
        broadcast_unicode(serial, body)
        time.sleep(1)
    finally:
        if original_ime and original_ime != "null":
            run_adb(serial, "shell", "ime", "set", original_ime, check=False)


def broadcast_unicode(serial: str, text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    run_adb(
        serial,
        "shell",
        "am",
        "broadcast",
        "-a",
        "ADB_INPUT_B64",
        "--es",
        "msg",
        encoded,
    )


def move_body_cursor_to_end(serial: str) -> None:
    """Place the rich-text cursor at the true end before inserting a topic."""
    root = dump_ui(serial)
    body = next((
        node for node in root.iter("node")
        if node.attrib.get("class", "").endswith("EditText")
        and node.attrib.get("hint", "").startswith("添加正文")
    ), None)
    if body is None:
        raise SystemExit("找不到正文输入框，无法把话题追加到正文末尾")
    tap_node(serial, body)
    time.sleep(0.3)
    run_adb(serial, "shell", "input", "keyevent", *(["20"] * 120))
    run_adb(serial, "shell", "input", "keyevent", "123")
    run_adb(serial, "shell", "input", "keyevent", "111", check=False)
    time.sleep(0.4)
    root = dump_ui(serial)
    editor_done = find_node(root, texts=("完成",))
    if editor_done is not None:
        tap_node(serial, editor_done)
        time.sleep(0.5)


def attach_topics(serial: str, topics: list[str]) -> dict[str, list[str]]:
    """Bind topics through Xiaohongshu suggestions instead of plain # text."""
    cleaned = [topic.strip().lstrip("#") for topic in topics if topic.strip()]
    if not cleaned:
        return {"bound": [], "skipped": []}
    bound: list[str] = []
    skipped: list[str] = []
    original_ime = current_ime(serial)
    try:
        root = dump_ui(serial)
        editor_done = find_node(root, texts=("完成",))
        if editor_done is not None:
            tap_node(serial, editor_done)
            time.sleep(0.6)

        run_adb(serial, "shell", "ime", "enable", ADB_IME)
        run_adb(serial, "shell", "ime", "set", ADB_IME)
        move_body_cursor_to_end(serial)
        for topic in cleaned:
            topic_entry = wait_node(serial, texts=("话题",), timeout=8)
            tap_node(serial, topic_entry)
            time.sleep(0.6)

            root = dump_ui(serial)
            candidate = topic_candidate(root, topic)
            if candidate is None:
                body = next((
                    node for node in root.iter("node")
                    if node.attrib.get("class", "").endswith("EditText")
                    and node.attrib.get("hint", "").startswith("添加正文")
                ), None)
                if body is None:
                    raise SystemExit(f"无法输入小红书话题：{topic}；已中止发布")
                broadcast_unicode(serial, topic)
                candidate, root = wait_topic_candidate(serial, topic)
            if candidate is None:
                body = next((
                    node for node in root.iter("node")
                    if node.attrib.get("class", "").endswith("EditText")
                    and node.attrib.get("hint", "").startswith("添加正文")
                ), None)
                if body is not None and body.attrib.get("text", "").endswith(f"#{topic}"):
                    run_adb(serial, "shell", "input", "keyevent", *(["67"] * (len(topic) + 1)))
                run_adb(serial, "shell", "input", "keyevent", "111", check=False)
                time.sleep(0.5)
                root = dump_ui(serial)
                editor_done = find_node(root, texts=("完成",))
                if editor_done is not None:
                    tap_node(serial, editor_done)
                    time.sleep(0.5)
                skipped.append(topic)
                _log(f"[话题] 未找到精确候选，已跳过：{topic}")
                continue

            tap_node(serial, candidate)
            return_control = wait_node(serial, texts=("完成", "话题"), timeout=8)
            if return_control.attrib.get("text") == "完成":
                tap_node(serial, return_control)
            time.sleep(0.6)
            bound.append(topic)
    finally:
        if original_ime and original_ime != "null":
            run_adb(serial, "shell", "ime", "set", original_ime, check=False)
        run_adb(serial, "shell", "input", "keyevent", "111", check=False)
    return {"bound": bound, "skipped": skipped}


def attach_product(serial: str, product_keyword: str) -> None:
    """Attach one exact product or fail closed before the publish tap."""
    keyword = product_keyword.strip()
    if not keyword:
        return
    # Close any lingering editor keyboard first. On some builds the keyboard
    # changes the compose scroll offset and hides the component row.
    run_adb(serial, "shell", "input", "keyevent", "111", check=False)
    time.sleep(0.5)
    entry = scroll_to_find_node(
        serial,
        texts=("添加商品", "关联商品", "商品"),
        descriptions=("添加商品", "关联商品", "商品"),
        max_scrolls=12,
    )
    if entry is None:
        component_entry = scroll_to_find_node(
            serial,
            texts=("添加组件",),
            descriptions=("添加组件",),
            max_scrolls=12,
        )
        if component_entry is not None:
            tap_node(serial, component_entry)
            time.sleep(0.8)
            # The component sheet is itself a scrollable list. Current XHS
            # builds expose the row as content-desc="商品" with no text.
            entry = scroll_to_find_node(
                serial,
                texts=("添加商品", "关联商品", "商品"),
                descriptions=("添加商品", "关联商品", "商品"),
                max_scrolls=12,
            )
    if entry is None:
        raise SystemExit("找不到“添加商品”入口，已中止发布，不会跳过挂车")
    tap_node(serial, entry)
    time.sleep(1.5)
    root = dump_ui(serial)
    search = product_search_control(root)
    if search is None:
        raise SystemExit("找不到商品搜索入口，已中止发布")
    if not search.attrib.get("class", "").endswith("EditText"):
        tap_node(serial, search)
        time.sleep(1)
        root = dump_ui(serial)
        search = next((
            node for node in root.iter("node")
            if node.attrib.get("class", "").endswith("EditText")
        ), None)
    if search is None:
        raise SystemExit("打开搜索后仍找不到商品输入框，已中止发布")
    original_ime = current_ime(serial)
    try:
        run_adb(serial, "shell", "ime", "enable", ADB_IME)
        run_adb(serial, "shell", "ime", "set", ADB_IME)
        tap_node(serial, search)
        broadcast_unicode(serial, keyword)
        run_adb(serial, "shell", "input", "keyevent", "66")
        time.sleep(2.5)
    finally:
        if original_ime and original_ime != "null":
            run_adb(serial, "shell", "ime", "set", original_ime, check=False)
    run_adb(serial, "shell", "input", "keyevent", "111", check=False)
    time.sleep(0.5)
    product, root = scroll_to_find_product_result(serial, keyword)
    if product is None:
        raise SystemExit(f"未找到匹配的挂车商品：{keyword}；已中止发布")
    container = clickable_ancestor(root, product)
    if container is None:
        raise SystemExit(f"无法定位商品勾选圆圈：{keyword}；已中止发布")
    container_bounds = tuple(
        int(value)
        for value in container.attrib["bounds"].replace("][", ",").strip("[]").split(",")
    )
    run_adb(serial, "shell", "input", "tap", str(container_bounds[0] + 60), str((container_bounds[1] + container_bounds[3]) // 2))
    time.sleep(1.2)
    root = dump_ui(serial)

    variant_confirm = find_node(root, texts=("确认并返回",))
    if variant_confirm is not None:
        tap_node(serial, variant_confirm)
        time.sleep(1.2)
        run_adb(serial, "shell", "input", "keyevent", "111", check=False)
        time.sleep(0.5)
        root = dump_ui(serial)

    selection_confirm = find_node(root, texts=("确认(", "确认添加"))
    if selection_confirm is not None:
        tap_node(serial, selection_confirm)
        time.sleep(1.5)
        root = dump_ui(serial)

    # Different XHS builds use different labels for the selected-product
    # counter and for the compose-page publish button.  Do not rely on one
    # exact string or on a single 1-second sleep here: the product sheet is
    # animated and its UI tree is often one or two dumps behind the screen.
    attached_count = _selected_product_marker(root)
    # 挂车确认后新版会先回到“添加组件”底部面板，再回到编辑正文页。
    # 该面板同样包含“商品/文件/活动”，不能仅凭工具栏文字判定已回编辑页。
    root = dump_ui(serial)
    for _ in range(3):
        if _is_compose_page(root) and not _is_component_sheet(root):
            break
        run_adb(serial, "shell", "input", "keyevent", "4", check=False)
        time.sleep(0.7)
        root = dump_ui(serial)
    if not _is_compose_page(root) or _is_component_sheet(root):
        visible = sorted({
            (node.attrib.get("text", "") or node.attrib.get("content-desc", "")).strip()
            for node in dump_ui(serial).iter("node")
            if (node.attrib.get("text", "") or node.attrib.get("content-desc", "")).strip()
        })
        raise SystemExit(
            f"商品已选择但未返回发布页：{keyword}；已中止发布。"
            f" 当前可见控件：{visible[:30]}"
        )
    # The compose screen is vertically scrollable.  After returning from the
    # product sheet some builds leave us near the bottom (showing 商品/文件/
    # 活动 but hiding the top 发布 button).  Restore the top before the caller
    # performs the final publish-button lookup.
    _scroll_compose_to_top(serial, root)


def _is_publish_page(root: ET.Element) -> bool:
    """Recognize the compose page across XHS label/resource-id variants."""
    for node in root.iter("node"):
        text = (node.attrib.get("text", "") or "").strip()
        description = (node.attrib.get("content-desc", "") or "").strip()
        resource_id = node.attrib.get("resource-id", "")
        if resource_id == "com.xingin.xhs:id/a5t":
            return True
        # Some builds expose the label on a non-clickable child while the
        # parent owns the actual tap target.
        if text in {"发布", "发布笔记"} or text.startswith("发布笔记"):
            return True
        if description in {"发布", "发布笔记"} or description.startswith("发布笔记"):
            return True
    # On the affected builds the publish button is above the current viewport
    # and is absent from the dump.  The compose toolbar still exposes several
    # stable actions; two or more together are sufficient to identify it and
    # are unlikely to occur in the product picker.
    compose_markers = {"商品", "引用笔记", "文件", "活动", "添加标题", "添加正文"}
    visible_markers = {
        (node.attrib.get("text", "") or node.attrib.get("content-desc", "")).strip()
        for node in root.iter("node")
    }
    if len(compose_markers & visible_markers) >= 2:
        return True
    return False


def _find_publish_button(root: ET.Element, *, scheduled: bool = False) -> ET.Element | None:
    """Locate the page-level publish action across XHS label variants."""
    labels = {"发布", "发布笔记", "发笔记"}
    if scheduled:
        labels.add("定时发布")
    candidates = [
        node for node in root.iter("node")
        if (
            node.attrib.get("text") in labels
            or node.attrib.get("content-desc") in labels
            or node.attrib.get("resource-id") == "com.xingin.xhs:id/a5t"
        )
    ]
    return candidates[-1] if candidates else None


def _is_compose_page(root: ET.Element) -> bool:
    """Recognize the editor before its final 发布 button is visible."""
    ids = {"com.xingin.xhs:id/b5i", "com.xingin.xhs:id/b4d"}
    has_title_or_body = any(
        node.attrib.get("resource-id") in ids
        or node.attrib.get("hint", "").startswith(("添加标题", "添加正文"))
        for node in root.iter("node")
    )
    toolbar = {"商品", "引用笔记", "文件", "活动", "添加"}
    labels = {
        (node.attrib.get("text", "") or node.attrib.get("content-desc", "")).strip()
        for node in root.iter("node")
    }
    return has_title_or_body or len(toolbar & labels) >= 2


def _is_component_sheet(root: ET.Element) -> bool:
    """Recognize the post-product "添加组件" bottom sheet.

    It reuses the same toolbar labels as the editor, but is anchored at the
    bottom of the screen and has no title/body editor fields.
    """
    labels = {
        (node.attrib.get("text", "") or node.attrib.get("content-desc", "")).strip()
        for node in root.iter("node")
    }
    if not {"商品", "文件", "引用笔记", "活动"}.issubset(labels):
        return False
    if any(
        node.attrib.get("resource-id") in {"com.xingin.xhs:id/b5i", "com.xingin.xhs:id/b4d"}
        or node.attrib.get("hint", "").startswith(("添加标题", "添加正文"))
        for node in root.iter("node")
    ):
        return False
    for node in root.iter("node"):
        if node.attrib.get("class") != "android.widget.ScrollView":
            continue
        try:
            _, top, _, _ = (
                int(value)
                for value in node.attrib["bounds"].replace("][", ",").strip("[]").split(",")
            )
        except (KeyError, ValueError):
            continue
        if top >= 850:
            return True
    return False


def _selected_product_marker(root: ET.Element) -> ET.Element | None:
    """Find selected-product counters used by different product-sheet builds."""
    markers = ("件商品", "商品已选", "已选商品", "商品(")
    for node in root.iter("node"):
        label = " ".join((node.attrib.get("text", ""), node.attrib.get("content-desc", ""))).strip()
        if any(marker in label for marker in markers):
            return node
    return None


def _wait_for_publish_page(serial: str, *, timeout: float = 8.0) -> ET.Element | None:
    """Wait for the compose page to settle after leaving the product sheet."""
    deadline = time.time() + timeout
    root = dump_ui(serial)
    while time.time() < deadline:
        if _is_publish_page(root):
            return root
        time.sleep(0.5)
        root = dump_ui(serial)

    # If the sheet is still open after its animation, one BACK returns to the
    # compose page. Only do this when the tree still looks like the product
    # picker; never press BACK blindly from an already-restored compose page.
    # If the product sheet is still open, one final BACK returns to compose.
    # Never press BACK from an unknown screen: that could leave the compose
    # flow and make the next publish operate on the wrong page.
    picker_open = (
        product_search_control(root) is not None
        or _selected_product_marker(root) is not None
        or find_node(root, texts=("确认并返回", "确认添加")) is not None
    )
    if picker_open:
        run_adb(serial, "shell", "input", "keyevent", "4", check=False)
        deadline = time.time() + 4.0
        while time.time() < deadline:
            time.sleep(0.5)
            root = dump_ui(serial)
            if _is_publish_page(root):
                return root
    return None


def _scroll_compose_to_top(serial: str, root: ET.Element) -> None:
    """Best-effort scroll to the top of the compose page after product attach."""
    for _ in range(4):
        container = _scrollable_node(root)
        if container is None:
            return
        try:
            left, top, right, bottom = (
                int(value)
                for value in container.attrib["bounds"].replace("][", ",").strip("[]").split(",")
            )
        except (KeyError, ValueError):
            return
        mid_x = (left + right) // 2
        run_adb(
            serial, "shell", "input", "swipe",
            str(mid_x), str(bottom - 80), str(mid_x), str(top + 80), "450",
            check=False,
        )
        time.sleep(0.45)
        root = dump_ui(serial)


def _fill_edit_text(serial: str, node: ET.Element, value: str) -> None:
    """Replace one native text field while preserving Chinese/date separators."""
    tap_node(serial, node)
    run_adb(serial, "shell", "input", "keyevent", "123")
    run_adb(serial, "shell", "input", "keyevent", *( ["67"] * 80 ))
    broadcast_unicode(serial, value)


def _select_schedule_wheel(
    serial: str,
    root: ET.Element,
    value: str,
    wheel_index: int,
    *,
    date_value: bool = False,
) -> ET.Element:
    """Select one visible item from the current date/time wheel."""
    def ordered_wheels(current_root: ET.Element) -> list[ET.Element]:
        wheels = [
            node for node in current_root.iter("node")
            if node.attrib.get("class") == "android.widget.ScrollView"
            and node.attrib.get("scrollable") == "true"
        ]
        # XML child order is not stable after a wheel is tapped. The three
        # columns are stable by horizontal position: date, hour, minute.
        return sorted(
            wheels,
            key=lambda node: int(
                node.attrib["bounds"].replace("][", ",").strip("[]").split(",")[0]
            ),
        )

    wheels = ordered_wheels(root)
    if wheel_index >= len(wheels):
        raise SystemExit("手机定时面板滚轮数量异常，已中止发布")
    wheel = wheels[wheel_index]
    left, top, right, bottom = (
        int(item)
        for item in wheel.attrib["bounds"].replace("][", ",").strip("[]").split(",")
    )

    def number(text: str) -> int | None:
        if date_value:
            match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
            return int(match.group(2)) if match else None
        return int(text) if text.isdigit() else None

    target_number = number(value)
    # 目标可能先以裁切行出现；按约一行的距离小步调整，每一步都重新
    # dump 并确认目标进入有效 bounds，避免一次大手势把目标滑过去。
    for _ in range(60):
        candidates = []
        for node in root.iter("node"):
            text = node.attrib.get("text", "").strip()
            if (value not in text if date_value else text != value):
                continue
            clickable = clickable_ancestor(root, node)
            action_node = clickable if clickable is not None else node
            bounds = node.attrib.get("bounds", "")
            try:
                x1, y1, x2, y2 = (
                    int(item) for item in bounds.replace("][", ",").strip("[]").split(",")
                )
            except (ValueError, AttributeError):
                continue
            try:
                ax1, ay1, ax2, ay2 = (
                    int(item)
                    for item in action_node.attrib.get("bounds", "").replace("][", ",").strip("[]").split(",")
                )
            except (ValueError, AttributeError):
                ax1 = ay1 = ax2 = ay2 = 0
            # Prefer the clickable row's bounds. The child TextView often has
            # an inverted clipped rectangle while its parent row is valid.
            if ax2 > ax1 and ay2 > ay1 and ax2 > left and ax1 < right and ay2 > top and ay1 < bottom:
                return action_node
            # If there is no usable clickable wrapper, accept only a valid
            # visible text rectangle; never tap an inverted residual node.
            if not (x2 > x1 and y2 > y1 and x2 > left and x1 < right):
                continue
            intersects = x2 > left and x1 < right and y2 > top and y1 < bottom
            if intersects:
                candidates.append(node)
        if candidates:
            selected = candidates[0]
            clickable = clickable_ancestor(root, selected)
            # Element 的 truth value 取决于子节点数量；可点击父节点常常
            # 是叶节点，会被 Python 当成 False。必须显式判断 None，不能
            # 使用 ``clickable or selected``，否则会退回到 bounds 异常的
            # 文本节点，出现“看见了但点不到”。
            if clickable is not None:
                try:
                    px1, py1, px2, py2 = (
                        int(item)
                        for item in clickable.attrib["bounds"].replace("][", ",").strip("[]").split(",")
                    )
                except (KeyError, ValueError):
                    px1 = py1 = px2 = py2 = 0
                # Some RN builds put an inverted bounds rectangle on the
                # clickable wrapper while the TextView child has valid bounds.
                # Never tap the wrapper in that case; use the visible text row.
                if px2 > px1 and py2 > py1:
                    return clickable
            return selected

        visible_numbers = []
        for node in root.iter("node"):
            n = number(node.attrib.get("text", "").strip())
            if n is None:
                continue
            bounds = node.attrib.get("bounds", "")
            try:
                x1, y1, x2, y2 = (
                    int(item) for item in bounds.replace("][", ",").strip("[]").split(",")
                )
            except (ValueError, AttributeError):
                continue
            center_y = (y1 + y2) // 2
            if not (x2 > x1 and y2 > y1):
                continue
            intersects = x2 > left and x1 < right and y2 > top and y1 < bottom
            if intersects:
                visible_numbers.append(n)
        # The selected wheel row is the item nearest the picker center line,
        # not the middle item in XML order (the list may be rendered in a
        # clipped/reordered sequence).
        center_value = None
        center_distance = None
        for node in root.iter("node"):
            n = number(node.attrib.get("text", "").strip())
            if n is None:
                continue
            try:
                x1, y1, x2, y2 = (
                    int(item) for item in node.attrib.get("bounds", "").replace("][", ",").strip("[]").split(",")
                )
            except (ValueError, AttributeError):
                continue
            if not (x2 > x1 and y2 > y1 and x2 > left and x1 < right and y2 > top and y1 < bottom):
                continue
            distance = abs(((y1 + y2) // 2) - ((top + bottom) // 2))
            if center_distance is None or distance < center_distance:
                center_value, center_distance = n, distance
        need_increase = target_number is not None and center_value is not None and target_number > center_value
        # Finger-up moves the wheel toward larger values; finger-down moves
        # toward smaller values.
        step = 85
        start_y = (top + bottom) // 2 + step if need_increase else (top + bottom) // 2 - step
        end_y = (top + bottom) // 2 - step if need_increase else (top + bottom) // 2 + step
        run_adb(
            serial, "shell", "input", "swipe", str((left + right) // 2),
            str(start_y), str((left + right) // 2), str(end_y), "320", check=False,
        )
        time.sleep(0.4)
        root = dump_ui(serial)
        wheels = ordered_wheels(root)
        if wheel_index >= len(wheels):
            raise SystemExit("手机定时面板滚轮数量异常，已中止发布")
        wheel = wheels[wheel_index]
        left, top, right, bottom = (
            int(item)
            for item in wheel.attrib["bounds"].replace("][", ",").strip("[]").split(",")
        )
    raise SystemExit(f"手机定时面板找不到目标时间项：{value}")


def schedule_on_phone(serial: str, scheduled_at: str) -> None:
    """Submit a schedule to XHS itself, instead of relying on our scheduler.

    XHS has shipped several variants of the More/定时发布 sheet.  We only
    continue when the sheet exposes semantic controls and editable date/time
    fields; otherwise we fail closed rather than accidentally publishing now.
    """
    try:
        target = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"无法解析手机定时发布时间：{scheduled_at}") from exc
    root = dump_ui(serial)
    # 新版编辑页从“高级选项”进入底部选项面板，面板里直接有
    # “定时发布”；旧版仍可能暴露名为“更多”的入口。
    schedule_entry = find_node(
        root,
        texts=("定时发布", "定时"),
        descriptions=("定时发布", "定时"),
    )
    if schedule_entry is None:
        entry = find_node(root, texts=("更多", "高级选项"), descriptions=("更多", "高级选项"))
        if entry is None:
            # attach_product restores the compose page to its top so the
            # publish control is available. “高级选项” is lower down on
            # current builds, so locate it with the compose scroll container.
            entry = scroll_to_find_node(
                serial,
                texts=("更多", "高级选项"),
                descriptions=("更多", "高级选项"),
                max_scrolls=8,
            )
        if entry is not None:
            tap_node(serial, entry)
            time.sleep(0.8)
            root = dump_ui(serial)
            schedule_entry = find_node(root, texts=("定时发布", "定时"), descriptions=("定时发布", "定时"))
    if schedule_entry is None:
        raise SystemExit("发布选项中找不到“定时发布”，已中止发布")
    tap_node(serial, schedule_entry)
    time.sleep(1.2)
    root = dump_ui(serial)

    date_text = target.strftime("%Y-%m-%d")
    time_text = target.strftime("%H:%M")
    edit_fields = [
        node for node in root.iter("node")
        if node.attrib.get("class", "").endswith("EditText")
    ]
    date_field = next((node for node in edit_fields if any(token in (node.attrib.get("hint", "") + node.attrib.get("text", "")) for token in ("日期", "年", "月", "日"))), None)
    time_field = next((node for node in edit_fields if any(token in (node.attrib.get("hint", "") + node.attrib.get("text", "")) for token in ("时间", "时", "分"))), None)
    if date_field is not None and time_field is not None:
        _fill_edit_text(serial, date_field, date_text)
        _fill_edit_text(serial, time_field, time_text)
    else:
        # 当前版本使用三个滚轮而非 EditText：日期（如“9月5日 周六”）、
        # 小时（“05”）和分钟（“00”）。目标日期通常在可见窗口内；
        # 不猜坐标，按 UI 文本定位后点击其可点击父节点。
        date_value = f"{target.month}月{target.day}日"
        for index, value, is_date in (
            (0, date_value, True),
            (1, target.strftime("%H"), False),
            (2, target.strftime("%M"), False),
        ):
            node = _select_schedule_wheel(serial, root, value, index, date_value=is_date)
            tap_node(serial, node)
            time.sleep(0.3)
            root = dump_ui(serial)
    run_adb(serial, "shell", "input", "keyevent", "111", check=False)
    time.sleep(0.5)
    root = dump_ui(serial)
    confirm = find_node(root, texts=("完成", "确定", "保存", "确认"), descriptions=("完成", "确定", "保存", "确认"))
    if confirm is None:
        raise SystemExit("手机定时面板找不到确认按钮，已中止发布")
    tap_node(serial, confirm)
    # 当前版本确认后会先回到“添加组件/高级选项”面板；该面板也有
    # “商品/文件/活动”文字，必须再返回一次才是真正的正文编辑页。
    for _ in range(4):
        time.sleep(0.7)
        root = dump_ui(serial)
        if _is_compose_page(root) and not _is_component_sheet(root):
            return
        run_adb(serial, "shell", "input", "keyevent", "4", check=False)
    raise SystemExit("手机定时设置后未返回正文编辑页，已中止发布")


def command_devices(_: argparse.Namespace) -> None:
    print(json.dumps(connected_devices(), ensure_ascii=False, indent=2))


def command_doctor(args: argparse.Namespace) -> None:
    require_serial(args.serial)
    android_user = getattr(args, "android_user", DEFAULT_ANDROID_USER) or DEFAULT_ANDROID_USER
    packages = run_adb(args.serial, "shell", "pm", "list", "packages", "--user", android_user, XHS_PACKAGE).stdout
    package_dump = run_adb(
        args.serial, "shell", "dumpsys", "package", XHS_PACKAGE
    ).stdout
    version_name = ""
    for line in package_dump.splitlines():
        if "versionName=" in line:
            version_name = line.split("versionName=", 1)[1].strip()
            break
    record = {
        "serial": args.serial,
        "android_user": android_user,
        "manufacturer": getprop(args.serial, "ro.product.manufacturer"),
        "model": getprop(args.serial, "ro.product.model"),
        "android": getprop(args.serial, "ro.build.version.release"),
        "sdk": getprop(args.serial, "ro.build.version.sdk"),
        "xiaohongshu_installed": f"package:{XHS_PACKAGE}" in packages,
        "xiaohongshu_version": version_name,
        "default_ime": current_ime(args.serial),
    }
    print(json.dumps(record, ensure_ascii=False, indent=2))


def command_install_ime(args: argparse.Namespace) -> None:
    require_serial(args.serial)
    apk = Path(__file__).resolve().parent.parent / "assets" / "ADBKeyboard.apk"
    run_adb(args.serial, "install", "-r", str(apk), capture=False)
    # HyperOS may install the APK with the package disabled until it is
    # explicitly enabled once.
    run_adb(
        args.serial,
        "shell",
        "pm",
        "enable",
        "com.android.adbkeyboard",
        capture=False,
        check=False,
    )
    run_adb(args.serial, "shell", "ime", "enable", ADB_IME, capture=False)
    print("ADBKeyBoard installed and enabled; it was not selected as default.")


def _log(msg: str) -> None:
    """Diagnostic output that survives subprocess capture — writes to stderr with flush."""
    print(msg, file=sys.stderr, flush=True)


def _set_remote_mtime(serial: str, remote_path: str, age_seconds: int) -> None:
    """Set a file's modification time on the phone so the gallery sorts it correctly.

    age_seconds=0 means "now" (newest), larger values make the file older.
    Uses ``touch -t`` which toybox/busybox support on Android 7+.
    Falls back to plain ``touch`` if ``-t`` is unavailable.
    """
    if age_seconds <= 0:
        run_adb(serial, "shell", "touch", remote_path, check=False)
        return
    import datetime
    ts = datetime.datetime.now() - datetime.timedelta(seconds=age_seconds)
    # touch -t format: [[CC]YY]MMDDhhmm[.ss]
    formatted = ts.strftime("%Y%m%d%H%M.%S")
    run_adb(serial, "shell", "touch", "-t", formatted, remote_path, check=False)


def command_push(args: argparse.Namespace) -> None:
    require_serial(args.serial)
    remote_dir = args.remote_dir.rstrip("/")
    run_adb(args.serial, "shell", "mkdir", "-p", remote_dir)
    if args.clear:
        run_adb(args.serial, "shell", "find", remote_dir, "-maxdepth", "1", "-type", "f", "-delete")
    # 倒序推送：先推最后一张，最后推第一张。多数安卓相册按"最新在前"排列，
    # 这样第一张最后入库、排在相册最前面。用户实测确认此手机相册就是最新在前。
    # 每张图之间加 2 秒间隔——此前全部在零点几秒内推完，时间戳几乎相同，
    # 相册分不出先后就乱排了（用户实测：首尾对、中间错）。
    indexed_images = list(enumerate(args.images, start=1))
    remote_paths: list[str] = []
    for index, source_name in reversed(indexed_images):
        source = Path(source_name).expanduser().resolve()
        if not source.is_file():
            raise SystemExit(f"Image not found: {source}")
        suffix = source.suffix.lower() or ".jpg"
        remote = f"{remote_dir}/{index:02d}_{source.stem}{suffix}"
        run_adb(args.serial, "push", str(source), remote, capture=False)
        run_adb(args.serial, "shell", "touch", remote)
        run_adb(
            args.serial,
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            f"file://{remote}",
        )
        # 给媒体扫描器时间处理这张图，再推下一张。
        # 没有这个间隔，多张图几乎同时入库，相册排序就不稳定。
        time.sleep(2)
        remote_paths.append(remote)
    for remote in reversed(remote_paths):
        print(remote)


def command_open(args: argparse.Namespace) -> None:
    require_serial(args.serial)
    android_user = getattr(args, "android_user", DEFAULT_ANDROID_USER) or DEFAULT_ANDROID_USER
    run_adb(args.serial, "shell", "am", "force-stop", "--user", android_user, XHS_PACKAGE)
    run_adb(args.serial, "shell", "am", "start", "--user", android_user, "-n", f"{XHS_PACKAGE}/.index.v2.IndexActivityV2", capture=True)


def command_calibrate(args: argparse.Namespace) -> None:
    """Validate a new device without selecting media or publishing."""
    require_serial(args.serial)
    # A harmless corner tap detects Xiaomi's separate USB security switch.
    try:
        run_adb(args.serial, "shell", "input", "tap", "5", "5")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            "Device rejects ADB input. Enable USB调试（安全设置） and reconnect."
        ) from exc
    root = enter_gallery(args.serial, getattr(args, "android_user", DEFAULT_ANDROID_USER) or DEFAULT_ANDROID_USER)
    thumbnails = selectable_thumbnails(root)
    if not thumbnails:
        raise SystemExit("Device calibration reached CodexXHS but found no selectable image")
    size_output = run_adb(args.serial, "shell", "wm", "size").stdout.strip()
    density_output = run_adb(args.serial, "shell", "wm", "density").stdout.strip()
    version = run_adb(
        args.serial, "shell", "dumpsys", "package", XHS_PACKAGE
    ).stdout
    version_name = ""
    for line in version.splitlines():
        if "versionName=" in line:
            version_name = line.split("versionName=", 1)[1].strip()
            break
    print(json.dumps({
        "status": "calibrated",
        "schema": CALIBRATION_SCHEMA,
        "serial": args.serial,
        "model": getprop(args.serial, "ro.product.model"),
        "screen_size": size_output,
        "density": density_output,
        "xiaohongshu_version": version_name,
        "gallery_control": "semantic",
        "thumbnail_count": len(thumbnails),
    }, ensure_ascii=False))


def command_fill(args: argparse.Namespace) -> None:
    require_serial(args.serial)
    title = Path(args.title_file).read_text(encoding="utf-8").strip()
    body = Path(args.body_file).read_text(encoding="utf-8").strip()
    fill_title_body(args.serial, title, body)
    topic_result = attach_topics(args.serial, topics_from_file(args.topics_file))
    print(json.dumps({"status": "filled", "topics": topic_result}, ensure_ascii=False))


def _match_thumbnails_to_order(
    thumbnails: list[ET.Element],
    image_count: int,
    root: ET.Element,
) -> list[ET.Element]:
    """Try to match thumbnails to the expected order (01_, 02_, …) by content-desc.

    If thumbnails expose filenames in content-desc, we can select in the exact
    intended order regardless of how the gallery sorts. If they don't, fall
    back to position order (top-to-bottom, left-to-right).
    """
    # Check if any thumbnail has a content-desc containing a filename index
    indexed: list[ET.Element | None] = [None] * image_count
    found_any = False
    for node in thumbnails:
        desc = node.attrib.get("content-desc", "") or node.attrib.get("text", "")
        for i in range(image_count):
            prefix = f"{i + 1:02d}_"
            if prefix in desc:
                indexed[i] = node
                found_any = True
                break

    if found_any and all(node is not None for node in indexed):
        return [node for node in indexed if node is not None]

    # Fallback: position order. Print a warning so we know matching didn't work.
    _log(
        f"[选图] 警告：无法通过 content-desc 匹配文件名，回退到位置排序。"
        f"缩略图数={len(thumbnails)}，需选={image_count}"
    )
    for i, node in enumerate(thumbnails[:image_count]):
        desc = node.attrib.get("content-desc", "") or node.attrib.get("text", "")
        _log(f"[选图] 回退第 {i+1} 张：desc={desc}")
    return thumbnails[:image_count]


def command_publish(args: argparse.Namespace) -> None:
    """Publish one reviewed payload, validating every Xiaohongshu screen."""
    require_serial(args.serial)
    if args.image_count < 1 or args.image_count > 9:
        raise SystemExit("image-count must be between 1 and 9")
    title = Path(args.title_file).read_text(encoding="utf-8").strip()
    body = Path(args.body_file).read_text(encoding="utf-8").strip()
    topics = topics_from_file(args.topics_file)
    if not title:
        raise SystemExit("Title cannot be empty")

    root = enter_gallery(args.serial, getattr(args, "android_user", DEFAULT_ANDROID_USER) or DEFAULT_ANDROID_USER)

    # The app-specific album is cleared before every run and contains only the
    # exact ordered payload for this job.
    root = dump_ui(args.serial)
    thumbnails = selectable_thumbnails(root)
    if len(thumbnails) < args.image_count:
        raise SystemExit(
            f"Gallery contains only {len(thumbnails)} selectable items; "
            f"{args.image_count} required"
        )
    # 不再盲目取前 N 个——尝试用 content-desc 里的文件名匹配预期顺序。
    # 缩略图的 content-desc 在部分小红书版本里包含文件名，能直接对应到推上去的图。
    selected = _match_thumbnails_to_order(thumbnails, args.image_count, root)
    for seq, node in enumerate(selected, start=1):
        bounds = node.attrib.get("bounds", "")
        desc = node.attrib.get("content-desc", "") or node.attrib.get("text", "")
        rid = node.attrib.get("resource-id", "")
        _log(f"[选图] 第 {seq} 张：bounds={bounds} desc={desc} rid={rid}")
    for node in selected:
        tap_thumbnail_selector(args.serial, node)
        time.sleep(0.3)

    # The gallery normally stays in multi-select mode until 下一步.  Some XHS
    # builds auto-open the editor immediately after selecting the only image;
    # in that case there is no 下一步 button, but the compose page is already
    # ready for filling.  Accept that path only for a one-image job and only
    # when the compose screen can be positively identified.
    root = dump_ui(args.serial)
    next_button = find_node(root, descriptions=("下一步",), texts=("下一步",))
    if next_button is None:
        if args.image_count != 1 or not _is_compose_page(root):
            raise SystemExit(
                f"未能以多选模式选中 {args.image_count} 张图片，已中止发布"
            )
        _log("[选图] 单图已自动进入编辑页，跳过下一步")
    else:
        tap_node(args.serial, next_button)
        time.sleep(2)
        done = wait_node(
            args.serial,
            descriptions=("完成", "下一步"),
            texts=("完成", "下一步"),
        )
        tap_node(args.serial, done)
        time.sleep(3)

    root = dump_ui(args.serial)
    recommendation_dialog = find_node(
        root,
        texts=("标题推荐功能需要获取", "申请获取您的相册内容位置信息"),
    )
    if recommendation_dialog is not None:
        cancel = find_node(root, texts=("取消",))
        if cancel is None:
            raise SystemExit("Cannot dismiss the title recommendation permission dialog")
        tap_node(args.serial, cancel)
        time.sleep(1)

    wait_node(
        args.serial,
        texts=("添加标题",),
        resource_ids=("com.xingin.xhs:id/b5i",),
    )
    fill_title_body(args.serial, title, body)
    # Restoring the original IME does not necessarily hide its input view.
    # ESC closes only the soft keyboard; BACK may navigate away from compose.
    run_adb(args.serial, "shell", "input", "keyevent", "111")
    time.sleep(1.5)
    root = dump_ui(args.serial)
    # Some builds keep the body editor in an expanded sub-mode after the IME
    # is restored. Its "完成" action must be applied before the page-level
    # publish button can receive taps.
    editor_done = find_node(root, texts=("完成",))
    if editor_done is not None:
        tap_node(args.serial, editor_done)
        time.sleep(1)
        root = dump_ui(args.serial)
    topic_result = attach_topics(args.serial, topics)
    attach_product(args.serial, args.product_keyword or "")
    if args.scheduled_at:
        schedule_on_phone(args.serial, args.scheduled_at)
    root = dump_ui(args.serial)
    publish_button = _find_publish_button(root, scheduled=bool(args.scheduled_at))
    if publish_button is None:
        raise SystemExit("Publish button is not present after filling the reviewed payload")
    if publish_button.attrib.get("clickable") != "true":
        clickable = clickable_ancestor(root, publish_button)
        if clickable is not None:
            publish_button = clickable
    tap_node(args.serial, publish_button)
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(1)
        root = dump_ui(args.serial)
        success_marker = find_node(
            root,
            texts=("发布成功", "发布中", "发布完成", "审核中", "正在发布", "已定时", "定时发布成功"),
            descriptions=("发布成功", "发布中", "发布完成", "审核中", "正在发布", "已定时", "定时发布成功"),
        )
        if success_marker is not None:
            signal = success_marker.attrib.get("text") or success_marker.attrib.get("content-desc") or ""
            scheduled = bool(args.scheduled_at) or "定时" in signal
            print(json.dumps({"status": "scheduled" if scheduled else "published", "image_count": args.image_count, "signal": signal, "topics": topic_result}, ensure_ascii=False))
            return
        title_field = find_node(
            root,
            texts=("添加标题",),
            resource_ids=("com.xingin.xhs:id/b5i",),
        )
        publish_texts = ("发布笔记", "发笔记", "定时发布") if args.scheduled_at else ("发布笔记", "发笔记")
        publish_control = find_node(
            root,
            texts=publish_texts,
            descriptions=("发布", "定时发布") if args.scheduled_at else ("发布",),
            resource_ids=("com.xingin.xhs:id/a5t",),
        )
        if title_field is None and publish_control is None:
            break
    else:
        raise SystemExit("Xiaohongshu publish result requires verification after tapping 发布笔记")
    print(json.dumps({"status": "scheduled" if args.scheduled_at else "published", "image_count": args.image_count, "topics": topic_result}, ensure_ascii=False))


def command_screenshot(args: argparse.Namespace) -> None:
    require_serial(args.serial)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        subprocess.run(
            [adb_path(), "-s", args.serial, "exec-out", "screencap", "-p"],
            check=True,
            stdout=handle,
        )
    print(output)


def _visible_strings(root: ET.Element) -> list[str]:
    values: list[str] = []
    for node in root.iter("node"):
        for key in ("text", "content-desc"):
            value = node.attrib.get(key, "").strip()
            if value and value not in values:
                values.append(value)
    return values


def _number_from_text(value: str) -> int:
    match = re.search(r"(\d+(?:\.\d+)?)\s*([万wW]?)", value)
    if not match:
        return 0
    number = float(match.group(1))
    if match.group(2):
        number *= 10000
    return int(number)


def _profile_metrics(values: list[str]) -> dict[str, int]:
    metrics = {"following": 0, "followers": 0, "likes_and_collects": 0}
    joined = "\n".join(values)
    patterns = {
        "following": (r"([\d.]+[万wW]?)\s*关注", r"关注\s*([\d.]+[万wW]?)"),
        "followers": (r"([\d.]+[万wW]?)\s*粉丝", r"粉丝\s*([\d.]+[万wW]?)"),
        "likes_and_collects": (r"([\d.]+[万wW]?)\s*获赞与收藏", r"获赞与收藏\s*([\d.]+[万wW]?)"),
    }
    for key, candidates in patterns.items():
        for pattern in candidates:
            match = re.search(pattern, joined)
            if match:
                metrics[key] = _number_from_text(match.group(1))
                break
    return metrics


def _note_candidates(root: ET.Element) -> list[dict[str, object]]:
    ignored = {
        "首页", "购物", "消息", "我", "笔记", "收藏", "赞过", "编辑资料",
        "关注", "粉丝", "获赞与收藏", "瞬间", "专辑",
    }
    found: list[dict[str, object]] = []
    for node in root.iter("node"):
        if node.attrib.get("clickable") != "true":
            continue
        text = (node.attrib.get("content-desc") or node.attrib.get("text") or "").strip()
        if not text or len(text) > 240:
            continue
        # On the signed-in profile, actual cards expose a stable accessibility
        # sentence beginning with 笔记/视频 and containing 来自{author}.
        card_match = re.match(r"^(?:笔记|视频)[,，](.*?)[,，]来自.+?[,，](\d+(?:\.\d+)?[万wW]?)赞", text)
        if not card_match:
            continue
        title = card_match.group(1).strip()
        if title in ignored or len(title) < 2:
            continue
        found.append({
            "title": title[:200],
            "likes": _number_from_text(card_match.group(2)),
            "views": _number_from_text((re.search(r"(\d+(?:\.\d+)?[万wW]?)阅读", text) or [None, "0"])[1]),
            "media_type": "video" if text.startswith("视频") else "image",
            "accessibility_text": text,
            "stable_key": re.sub(r"\s+", " ", title).strip()[:200],
        })
    unique: dict[str, dict[str, object]] = {}
    for item in found:
        unique.setdefault(str(item.get("stable_key") or item["title"]), item)
    return list(unique.values())


def _write_scan_checkpoint(path_text: str, payload: dict[str, object]) -> None:
    if not path_text:
        return
    path = Path(path_text)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def command_scan_profile(args: argparse.Namespace) -> None:
    """Read the signed-in account profile and visible note cards via UI."""
    require_serial(args.serial)
    run_adb(args.serial, "shell", "input", "keyevent", "224", check=False)
    keyguard = run_adb(args.serial, "shell", "dumpsys", "window").stdout
    if "mDreamingLockscreen=true" in keyguard or "isStatusBarKeyguard=true" in keyguard:
        raise SystemExit("Device is locked. Unlock the phone and keep Xiaohongshu available.")
    command_open(args)
    time.sleep(2)
    root = dump_ui(args.serial)
    already_profile = find_node(root, descriptions=("个人页头图", "已选定笔记")) is not None
    if not already_profile:
        mine_candidates = [
            node for node in root.iter("node")
            if node.attrib.get("text") == "我" or node.attrib.get("content-desc") in {"我", "我的"}
        ]
        mine = max(mine_candidates, key=lambda node: center(node.attrib["bounds"])[1]) if mine_candidates else None
        if mine is None:
            raise SystemExit("Cannot find the Xiaohongshu 我的 tab; device read calibration is required")
        tap_node(args.serial, mine)
        time.sleep(2)
    # Profile tabs remember their previous scroll offset. Always restore the
    # top before reading account identity and totals. Use the actual notes
    # container bounds; hardcoded coordinates can fall outside smaller screens.
    if not args.identity_only:
        for _ in range(3):
            root = dump_ui(args.serial)
            if not _swipe_scrollable_container(args.serial, root, direction="down", duration_ms=450):
                break
            time.sleep(0.4)
    root = dump_ui(args.serial)
    initial_values = _visible_strings(root)
    if not args.identity_only and not any("粉丝" in value or "获赞与收藏" in value for value in initial_values):
        raise SystemExit("Xiaohongshu profile page is not readable; unlock/login and retry")
    if args.identity_only:
        metrics = _profile_metrics(initial_values)
        red_id = next((value.split("：", 1)[-1].strip() for value in initial_values if "小红书号" in value), "")
        excluded = {"关注", "粉丝", "获赞与收藏", "编辑资料", "笔记", "收藏", "赞过"}
        avatar_label = next((value for value in initial_values if value.startswith("头像,")), "")
        nickname = avatar_label.split(",", 1)[1].strip() if "," in avatar_label else next((
            value for value in initial_values
            if value not in excluded and 2 <= len(value) <= 40
            and "小红书号" not in value and not value.startswith("头像,")
            and not re.fullmatch(r"[\d.万wW]+", value)
        ), "")
        print(json.dumps({
            "status": "scanned", "serial": args.serial,
            "android_user": getattr(args, "android_user", DEFAULT_ANDROID_USER) or DEFAULT_ANDROID_USER,
            "profile": {"nickname": nickname, "red_id": red_id, **metrics},
            "notes": [], "visible_note_count": 0,
            "expected_public_count": 0, "scroll_count": 0,
            "end_reason": "identity_only",
        }, ensure_ascii=False))
        return
    public_match = next((re.search(r"公开\s*(\d+)", value) for value in initial_values if "公开" in value), None)
    expected_public = int(public_match.group(1)) if public_match else 0
    notes: dict[str, dict[str, object]] = {}
    checkpoint_file = str(getattr(args, "checkpoint_file", "") or "")
    if checkpoint_file:
        try:
            previous = json.loads(Path(checkpoint_file).read_text(encoding="utf-8"))
            for item in previous.get("notes", []):
                if isinstance(item, dict) and item.get("title"):
                    notes[str(item.get("stable_key") or item["title"])] = item
        except (OSError, json.JSONDecodeError):
            pass
    # Android UI dump can briefly expose the same rendered rows while the
    # RecyclerView is loading the next batch. Five unchanged rounds (the old
    # threshold) stopped large profiles far too early, sometimes after only
    # a few dozen notes. Allow a longer settling window and rely on the hard
    # scan limit when the app does not expose an exact end marker.
    stagnant_rounds = 0
    resume_scroll_count = max(0, int(getattr(args, "resume_scroll_count", 0) or 0))
    scroll_count = resume_scroll_count
    end_reason = "max_scrolls"
    max_notes = max(1, int(getattr(args, "max_notes", 500) or 500))
    for _ in range(resume_scroll_count):
        root = dump_ui(args.serial)
        if not _swipe_scrollable_container(args.serial, root, direction="up"):
            scroll_count = _
            break
        time.sleep(1.2)
    for index in range(max(1, args.max_scrolls + 1)):
        root = dump_ui(args.serial)
        before = len(notes)
        for item in _note_candidates(root):
            notes.setdefault(str(item.get("stable_key") or item["title"]), item)
        if len(notes) == before:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        _write_scan_checkpoint(checkpoint_file, {
            "status": "scanning", "serial": args.serial,
            "android_user": getattr(args, "android_user", DEFAULT_ANDROID_USER) or DEFAULT_ANDROID_USER,
            "notes": list(notes.values())[:max_notes], "visible_note_count": min(len(notes), max_notes),
            "expected_public_count": expected_public, "scroll_count": scroll_count,
            "end_reason": "in_progress",
        })
        if len(notes) >= max_notes:
            end_reason = "max_notes_reached"
            break
        if stagnant_rounds >= 20:
            end_reason = "list_end_reached"
            break
        if index < args.max_scrolls:
            if not _swipe_scrollable_container(args.serial, root, direction="up"):
                end_reason = "notes_container_not_found"
                break
            scroll_count += 1
            _write_scan_checkpoint(checkpoint_file, {
                "status": "scanning", "serial": args.serial,
                "android_user": getattr(args, "android_user", DEFAULT_ANDROID_USER) or DEFAULT_ANDROID_USER,
                "notes": list(notes.values())[:max_notes], "visible_note_count": min(len(notes), max_notes),
                "expected_public_count": expected_public, "scroll_count": scroll_count,
                "end_reason": "in_progress",
            })
            time.sleep(1.8)
    # Return to the top so a sync does not leave the user deep in the profile.
    for _ in range(3):
        root = dump_ui(args.serial)
        if not _swipe_scrollable_container(args.serial, root, direction="down", duration_ms=450):
            break
        time.sleep(0.4)
    profile_values = initial_values
    metrics = _profile_metrics(profile_values)
    red_id = next((value.split("：", 1)[-1].strip() for value in profile_values if "小红书号" in value), "")
    excluded = {"关注", "粉丝", "获赞与收藏", "编辑资料", "笔记", "收藏", "赞过"}
    avatar_label = next((value for value in profile_values if value.startswith("头像,")), "")
    nickname = avatar_label.split(",", 1)[1].strip() if "," in avatar_label else next((
        value for value in profile_values
        if value not in excluded and 2 <= len(value) <= 40
        and "小红书号" not in value and not value.startswith("头像,")
        and not re.fullmatch(r"[\d.万wW]+", value)
    ), "")
    print(json.dumps({
        "status": "scanned", "serial": args.serial,
        "android_user": getattr(args, "android_user", DEFAULT_ANDROID_USER) or DEFAULT_ANDROID_USER,
        "profile": {"nickname": nickname, "red_id": red_id, **metrics},
        "notes": list(notes.values())[:max_notes], "visible_note_count": min(len(notes), max_notes),
        "expected_public_count": expected_public, "scroll_count": scroll_count,
        "end_reason": end_reason,
    }, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(required=True)

    devices = subparsers.add_parser("devices")
    devices.set_defaults(func=command_devices)

    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--serial", required=True)
    doctor.add_argument("--android-user", default=DEFAULT_ANDROID_USER)
    doctor.set_defaults(func=command_doctor)

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--serial", required=True)
    calibrate.add_argument("--android-user", default=DEFAULT_ANDROID_USER)
    calibrate.set_defaults(func=command_calibrate)

    install_ime = subparsers.add_parser("install-ime")
    install_ime.add_argument("--serial", required=True)
    install_ime.set_defaults(func=command_install_ime)

    push = subparsers.add_parser("push")
    push.add_argument("--serial", required=True)
    push.add_argument(
        "--remote-dir",
        default="/sdcard/Pictures/CodexXHS",
    )
    push.add_argument("--clear", action="store_true")
    push.add_argument("images", nargs="+")
    push.set_defaults(func=command_push)

    open_app = subparsers.add_parser("open")
    open_app.add_argument("--serial", required=True)
    open_app.add_argument("--android-user", default=DEFAULT_ANDROID_USER)
    open_app.set_defaults(func=command_open)

    fill = subparsers.add_parser("fill")
    fill.add_argument("--serial", required=True)
    fill.add_argument("--title-file", required=True)
    fill.add_argument("--body-file", required=True)
    fill.add_argument("--topics-file")
    fill.add_argument("--android-user", default=DEFAULT_ANDROID_USER)
    fill.set_defaults(func=command_fill)

    screenshot = subparsers.add_parser("screenshot")
    screenshot.add_argument("--serial", required=True)
    screenshot.add_argument("--output", required=True)
    screenshot.set_defaults(func=command_screenshot)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--serial", required=True)
    publish.add_argument("--title-file", required=True)
    publish.add_argument("--body-file", required=True)
    publish.add_argument("--topics-file")
    publish.add_argument("--image-count", required=True, type=int)
    publish.add_argument("--product-keyword", default="")
    publish.add_argument("--scheduled-at", default="", help="让小红书手机端保存的定时发布时间，格式 YYYY-MM-DDTHH:MM")
    publish.add_argument("--android-user", default=DEFAULT_ANDROID_USER)
    publish.set_defaults(func=command_publish)

    scan_profile = subparsers.add_parser("scan-profile")
    scan_profile.add_argument("--serial", required=True)
    scan_profile.add_argument("--android-user", default=DEFAULT_ANDROID_USER)
    scan_profile.add_argument("--max-scrolls", type=int, default=260)
    scan_profile.add_argument("--max-notes", type=int, default=500)
    scan_profile.add_argument("--checkpoint-file", default="")
    scan_profile.add_argument("--resume-scroll-count", type=int, default=0)
    scan_profile.add_argument("--identity-only", action="store_true", help="只读取账号身份，不读取笔记或滑动列表")
    scan_profile.set_defaults(func=command_scan_profile)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
