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
from pathlib import Path

# Windows 默认用 GBK 编码，手机返回的中文和 emoji 会崩。
# 强制 stdout/stderr 用 UTF-8，后端 subprocess 也用 UTF-8 读。
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


XHS_PACKAGE = "com.xingin.xhs"
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
        if width >= 250 and height >= 250 and abs(width - height) <= 40:
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


def enter_gallery(serial: str) -> ET.Element:
    """Start a note and stop safely in the dedicated image album."""
    run_adb(serial, "shell", "am", "force-stop", XHS_PACKAGE)
    command_open(type("Args", (), {"serial": serial})())
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


def attach_product(serial: str, product_keyword: str) -> None:
    """Attach one exact product or fail closed before the publish tap."""
    keyword = product_keyword.strip()
    if not keyword:
        return
    root = dump_ui(serial)
    entry = find_node(
        root,
        texts=("添加商品", "关联商品", "商品"),
        descriptions=("添加商品", "关联商品"),
    )
    if entry is None:
        component_entry = find_node(root, texts=("添加组件",), descriptions=("添加组件",))
        if component_entry is not None:
            tap_node(serial, component_entry)
            time.sleep(0.8)
            root = dump_ui(serial)
            entry = find_node(root, texts=("商品",), descriptions=("商品",))
    if entry is None:
        raise SystemExit("找不到“添加商品”入口，已中止发布，不会跳过挂车")
    tap_node(serial, entry)
    time.sleep(1.5)
    root = dump_ui(serial)
    search = next((
        node for node in root.iter("node")
        if node.attrib.get("class", "").endswith("EditText")
        and "搜索" in (node.attrib.get("text", "") + node.attrib.get("hint", "") + node.attrib.get("content-desc", ""))
    ), None)
    if search is None:
        # XHS 9.40 exposes the search affordance as an unlabeled clickable
        # ImageView to the right of the product category tabs.
        search_icon = next((
            node for node in root.iter("node")
            if node.attrib.get("class", "").endswith("ImageView")
            and node.attrib.get("clickable") == "true"
            and (lambda b: b[0] >= 900 and 240 <= b[1] <= 420)(
                tuple(int(v) for v in node.attrib.get("bounds", "[0,0][0,0]").replace("][", ",").strip("[]").split(","))
            )
        ), None)
        if search_icon is None:
            raise SystemExit("找不到商品搜索入口，已中止发布")
        tap_node(serial, search_icon)
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
    root = dump_ui(serial)
    exact = next((node for node in root.iter("node") if node.attrib.get("text", "").strip() == keyword), None)
    if exact is None:
        raise SystemExit(f"未找到精确匹配的挂车商品：{keyword}；已中止发布")
    exact_bounds = tuple(int(v) for v in exact.attrib["bounds"].replace("][", ",").strip("[]").split(","))
    containers = []
    for node in root.iter("node"):
        if node.attrib.get("clickable") != "true" or "bounds" not in node.attrib:
            continue
        bounds = tuple(int(v) for v in node.attrib["bounds"].replace("][", ",").strip("[]").split(","))
        if bounds[0] <= exact_bounds[0] and bounds[1] <= exact_bounds[1] and bounds[2] >= exact_bounds[2] and bounds[3] >= exact_bounds[3]:
            containers.append((max(1, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])), bounds))
    if not containers:
        raise SystemExit(f"无法定位商品勾选圆圈：{keyword}；已中止发布")
    _, product_bounds = min(containers, key=lambda item: item[0])
    run_adb(serial, "shell", "input", "tap", str(product_bounds[0] + 60), str((product_bounds[1] + product_bounds[3]) // 2))
    time.sleep(1.2)
    root = dump_ui(serial)
    confirm = find_node(root, texts=("确认添加", "完成", "添加"))
    if confirm is not None:
        tap_node(serial, confirm)
        time.sleep(1.5)
    root = dump_ui(serial)
    if find_node(root, texts=(keyword,)) is None:
        raise SystemExit(f"商品未在发布页确认显示：{keyword}；已中止发布")


def command_devices(_: argparse.Namespace) -> None:
    print(json.dumps(connected_devices(), ensure_ascii=False, indent=2))


def command_doctor(args: argparse.Namespace) -> None:
    require_serial(args.serial)
    packages = run_adb(args.serial, "shell", "pm", "list", "packages", XHS_PACKAGE).stdout
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
    run_adb(
        args.serial,
        "shell",
        "monkey",
        "-p",
        XHS_PACKAGE,
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
        capture=True,
    )


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
    root = enter_gallery(args.serial)
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
    print("Filled title/body and restored the original input method.")


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
    if not title:
        raise SystemExit("Title cannot be empty")

    root = enter_gallery(args.serial)

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

    # The gallery must still be visible after all taps. If a single-photo
    # picker jumped directly to editing, stop before filling/publishing.
    root = dump_ui(args.serial)
    next_button = find_node(root, descriptions=("下一步",), texts=("下一步",))
    if next_button is None:
        raise SystemExit(
            f"未能以多选模式选中 {args.image_count} 张图片，已中止发布"
        )

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
    attach_product(args.serial, args.product_keyword or "")
    root = dump_ui(args.serial)
    publish_candidates = [
        node for node in root.iter("node")
        if (
            node.attrib.get("text") in {"发布", "发布笔记"}
            or node.attrib.get("content-desc") == "发布"
            or node.attrib.get("resource-id") == "com.xingin.xhs:id/a5t"
        )
        and node.attrib.get("clickable") == "true"
    ]
    publish_button = publish_candidates[-1] if publish_candidates else None
    if publish_button is None:
        raise SystemExit("Publish button is not present after filling the reviewed payload")
    tap_node(args.serial, publish_button)
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(1)
        root = dump_ui(args.serial)
        success_marker = find_node(
            root,
            texts=("发布成功", "发布中", "发布完成", "审核中", "正在发布"),
            descriptions=("发布成功", "发布中", "发布完成", "审核中", "正在发布"),
        )
        if success_marker is not None:
            print(json.dumps({"status": "published", "image_count": args.image_count, "signal": success_marker.attrib.get("text") or success_marker.attrib.get("content-desc")}, ensure_ascii=False))
            return
        title_field = find_node(
            root,
            texts=("添加标题",),
            resource_ids=("com.xingin.xhs:id/b5i",),
        )
        publish_control = find_node(
            root,
            texts=("发布笔记",),
            descriptions=("发布",),
            resource_ids=("com.xingin.xhs:id/a5t",),
        )
        if title_field is None and publish_control is None:
            break
    else:
        raise SystemExit("Xiaohongshu publish result requires verification after tapping 发布笔记")
    print(json.dumps({"status": "published", "image_count": args.image_count}, ensure_ascii=False))


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
        })
    unique: dict[str, dict[str, object]] = {}
    for item in found:
        unique.setdefault(str(item["title"]), item)
    return list(unique.values())


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
    # top before reading account identity and totals.
    for _ in range(3):
        run_adb(args.serial, "shell", "input", "swipe", "540", "650", "540", "2100", "450", check=False)
        time.sleep(0.4)
    root = dump_ui(args.serial)
    initial_values = _visible_strings(root)
    if not any("粉丝" in value or "获赞与收藏" in value for value in initial_values):
        raise SystemExit("Xiaohongshu profile page is not readable; unlock/login and retry")
    public_match = next((re.search(r"公开\s*(\d+)", value) for value in initial_values if "公开" in value), None)
    expected_public = int(public_match.group(1)) if public_match else 0
    notes: dict[str, dict[str, object]] = {}
    stagnant_rounds = 0
    scroll_count = 0
    end_reason = "max_scrolls"
    for index in range(max(1, args.max_scrolls + 1)):
        root = dump_ui(args.serial)
        before = len(notes)
        for item in _note_candidates(root):
            notes.setdefault(str(item["title"]), item)
        if len(notes) == before:
            stagnant_rounds += 1
        else:
            stagnant_rounds = 0
        if expected_public and len(notes) >= expected_public:
            end_reason = "expected_count_reached"
            break
        if stagnant_rounds >= 5:
            end_reason = "list_end_reached"
            break
        if index < args.max_scrolls:
            run_adb(args.serial, "shell", "input", "swipe", "540", "1900", "540", "650", "650")
            scroll_count += 1
            time.sleep(1.5)
    # Return to the top so a sync does not leave the user deep in the profile.
    run_adb(args.serial, "shell", "input", "swipe", "540", "650", "540", "2050", "450", check=False)
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
        "profile": {"nickname": nickname, "red_id": red_id, **metrics},
        "notes": list(notes.values()), "visible_note_count": len(notes),
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
    doctor.set_defaults(func=command_doctor)

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--serial", required=True)
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
    open_app.set_defaults(func=command_open)

    fill = subparsers.add_parser("fill")
    fill.add_argument("--serial", required=True)
    fill.add_argument("--title-file", required=True)
    fill.add_argument("--body-file", required=True)
    fill.set_defaults(func=command_fill)

    screenshot = subparsers.add_parser("screenshot")
    screenshot.add_argument("--serial", required=True)
    screenshot.add_argument("--output", required=True)
    screenshot.set_defaults(func=command_screenshot)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--serial", required=True)
    publish.add_argument("--title-file", required=True)
    publish.add_argument("--body-file", required=True)
    publish.add_argument("--image-count", required=True, type=int)
    publish.add_argument("--product-keyword", default="")
    publish.set_defaults(func=command_publish)

    scan_profile = subparsers.add_parser("scan-profile")
    scan_profile.add_argument("--serial", required=True)
    scan_profile.add_argument("--max-scrolls", type=int, default=120)
    scan_profile.set_defaults(func=command_scan_profile)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
