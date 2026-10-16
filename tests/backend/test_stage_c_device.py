"""阶段 C：设备助手。覆盖安卓脚本的仓库内自持（BUG-03）与网页内自助连机（C-02）。"""

import urllib.error
import threading
import zipfile
from pathlib import Path

import pytest

from backend.app.services import android_xhs_service as android_service_module

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDORED_DIR = REPO_ROOT / "scripts" / "android"


def _write_platform_tools_zip(archive: Path, member_name: str) -> None:
    """造一个几 KB 的假安装包。测试绝不真的下载几十 MB 的官方包。"""
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"platform-tools/{member_name}", "fake-adb-binary")


def test_android_script_is_vendored_in_repo():
    """脚本必须在仓库内。此前只存在于 ~/.codex/skills/，新环境 clone 后安卓功能
    静默失效且无任何构建或测试报错（BUG-03）。"""
    script = VENDORED_DIR / "scripts" / "xhs_android.py"
    assert script.is_file(), f"缺少仓库内的安卓脚本：{script}"
    assert script.stat().st_size > 10_000, "脚本大小异常，可能是空占位文件"


def test_android_publish_lock_serializes_same_physical_device():
    """同一手机的两个发布调用必须排队，不能并发操作前台小红书。"""
    from backend.app.services.android_xhs_service import android_device_publish_lock

    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    order: list[str] = []

    def first() -> None:
        with android_device_publish_lock("PHONE-SERIAL"):
            order.append("first")
            first_entered.set()
            release_first.wait(timeout=2)

    def second() -> None:
        with android_device_publish_lock("PHONE-SERIAL"):
            order.append("second")
            second_entered.set()

    worker_one = threading.Thread(target=first)
    worker_two = threading.Thread(target=second)
    worker_one.start()
    assert first_entered.wait(timeout=1)
    worker_two.start()
    assert not second_entered.wait(timeout=0.1), "第二个任务不应抢占正在使用的手机"
    release_first.set()
    worker_one.join(timeout=2)
    worker_two.join(timeout=2)

    assert order == ["first", "second"]


def test_android_setup_script_and_apk_are_vendored():
    """xhs_android.py 在启用输入法时会读 ../assets/ADBKeyboard.apk，
    setup_android.py 负责安装 platform-tools。两者缺任一都会在运行时才炸。"""
    assert (VENDORED_DIR / "scripts" / "setup_android.py").is_file()
    apk = VENDORED_DIR / "assets" / "ADBKeyboard.apk"
    assert apk.is_file(), f"缺少 APK：{apk}"
    assert apk.read_bytes()[:2] == b"PK", "APK 应是 zip 格式，文件可能损坏"


def test_apk_resolves_relative_to_vendored_script():
    """脚本用 Path(__file__).parent.parent / "assets" / "ADBKeyboard.apk" 定位 APK，
    所以 scripts/ 与 assets/ 的相对层级必须保持。"""
    script = VENDORED_DIR / "scripts" / "xhs_android.py"
    expected_apk = script.resolve().parent.parent / "assets" / "ADBKeyboard.apk"
    assert expected_apk.is_file(), f"按脚本内的相对路径解析不到 APK：{expected_apk}"


def test_service_prefers_vendored_script_over_external_copy(monkeypatch):
    """仓库内副本必须优先于 ~/.codex 那份，否则交付出去的版本仍依赖开发机。"""
    monkeypatch.delenv("XHS_ANDROID_SCRIPT", raising=False)

    from backend.app.services.android_xhs_service import AndroidXhsService

    resolved = AndroidXhsService().script
    assert resolved == (VENDORED_DIR / "scripts" / "xhs_android.py").resolve(), (
        f"解析到了 {resolved}，应优先用仓库内副本"
    )


def test_env_override_still_wins(monkeypatch, tmp_path):
    """显式指定的路径优先级最高，便于本地调试改版脚本。"""
    custom = tmp_path / "custom_xhs_android.py"
    custom.write_text("# 测试用占位\n", encoding="utf-8")
    monkeypatch.setenv("XHS_ANDROID_SCRIPT", str(custom))

    from backend.app.services.android_xhs_service import AndroidXhsService

    assert AndroidXhsService().script == custom.resolve()


# --- C-02：连手机全流程网页内自助完成 ---------------------------------


@pytest.fixture
def isolated_service(monkeypatch, tmp_path):
    """把 adb 安装目录指向临时目录，且屏蔽真机上真实存在的 adb。"""
    from backend.app.services.android_xhs_service import AndroidXhsService

    monkeypatch.delenv("XHS_ADB", raising=False)
    monkeypatch.setenv("XHS_ADB_INSTALL_DIR", str(tmp_path / "install"))
    monkeypatch.setattr(android_service_module.shutil, "which", lambda _name: None)
    # 任务表是模块级的，必须逐个测试清空，否则上一个测试留下的 pending 任务
    # 会被复用，后面的测试全部拿到陈旧状态。
    android_service_module.adb_install_registry.reset()
    return AndroidXhsService()


def test_adb_path_finds_adb_exe_on_windows_layout(isolated_service, tmp_path):
    """Windows 上可执行文件叫 adb.exe。此前候选只拼了 `adb`，装好了也找不到。
    这里不 mock sys.platform，只造出 Windows 的文件布局——macOS 行为因此不变。"""
    tools = tmp_path / "install" / "platform-tools"
    tools.mkdir(parents=True)
    windows_adb = tools / "adb.exe"
    windows_adb.write_bytes(b"MZ")

    assert isolated_service._adb_path() == str(windows_adb.resolve())


def test_adb_path_still_finds_extensionless_adb_on_unix(isolated_service, tmp_path):
    tools = tmp_path / "install" / "platform-tools"
    tools.mkdir(parents=True)
    unix_adb = tools / "adb"
    unix_adb.write_bytes(b"\x7fELF")

    assert isolated_service._adb_path() == str(unix_adb.resolve())


def test_adb_path_is_none_when_nothing_installed(isolated_service):
    assert isolated_service._adb_path() is None


def test_setup_status_reports_installable_platform(isolated_service):
    status = isolated_service.setup_status()

    assert status["adb_available"] is False
    assert status["platform"] in {"Darwin", "Linux", "Windows"}
    assert status["adb_installable"] is True
    assert status["ime_installed"] is False
    assert status["ime_checked"] is False


def test_adb_install_does_not_download_again_when_already_installed(isolated_service, tmp_path):
    """幂等：重复点安装不能重复下载几十 MB。"""
    tools = tmp_path / "install" / "platform-tools"
    tools.mkdir(parents=True)
    (tools / "adb").write_bytes(b"\x7fELF")
    calls: list[str] = []
    isolated_service._download_platform_tools = lambda url, archive: calls.append(url)

    result = isolated_service.start_adb_install()

    assert result["status"] == "success"
    assert result["already_installed"] is True
    assert calls == [], "已安装时不得发起任何下载"


def test_adb_install_reuses_running_task_instead_of_downloading_twice(isolated_service):
    """两次点击必须拿到同一个任务 id，且只启动一个下载。"""
    started: list[str] = []

    def spawn(runner):
        started.append("spawned")
        del runner  # 不真的执行，模拟任务还在跑

    first = isolated_service.start_adb_install(spawn=spawn)
    second = isolated_service.start_adb_install(spawn=spawn)

    assert first["id"] == second["id"]
    assert started == ["spawned"], "第二次点击不应再启动一个下载任务"


def test_adb_install_success_records_path_without_real_download(isolated_service, tmp_path):
    """全程 mock 下载，绝不访问外网。"""
    install_root = tmp_path / "install"

    def fake_download(url, archive):
        del url
        _write_platform_tools_zip(archive, "adb")

    isolated_service._download_platform_tools = fake_download
    task = isolated_service.start_adb_install(spawn=lambda runner: runner())

    final = isolated_service.adb_install_status(task["id"])
    assert final["status"] == "success", final
    assert final["progress"] == 100
    assert final["adb_path"] == str((install_root / "platform-tools" / "adb").resolve())


def test_adb_install_failure_reports_readable_reason_not_raw_exception(isolated_service):
    """无网络时要给人话，不能把 URLError 原样丢给用户。"""

    def fake_download(url, archive):
        del url, archive
        raise urllib.error.URLError("[Errno 8] nodename nor servname provided")

    isolated_service._download_platform_tools = fake_download
    task = isolated_service.start_adb_install(spawn=lambda runner: runner())

    final = isolated_service.adb_install_status(task["id"])
    assert final["status"] == "failed"
    assert "连不上谷歌下载服务器" in final["error"]
    assert "Errno" not in final["error"], "不得泄漏原始异常文本"


def test_adb_install_failure_reports_disk_full_distinctly(isolated_service):
    def fake_download(url, archive):
        del url, archive
        raise OSError(28, "No space left on device")

    isolated_service._download_platform_tools = fake_download
    task = isolated_service.start_adb_install(spawn=lambda runner: runner())

    assert "磁盘空间不足" in isolated_service.adb_install_status(task["id"])["error"]


def test_adb_install_rejects_archive_without_adb(isolated_service):
    def fake_download(url, archive):
        del url
        _write_platform_tools_zip(archive, "not-adb.txt")

    isolated_service._download_platform_tools = fake_download
    task = isolated_service.start_adb_install(spawn=lambda runner: runner())

    final = isolated_service.adb_install_status(task["id"])
    assert final["status"] == "failed"
    assert "没有 adb" in final["error"]


def test_install_ime_passes_serial_to_vendored_script(isolated_service):
    """必须带 --serial 指定设备，不能操作「第一台」。"""
    isolated_service.devices = lambda: [{"serial": "PHONE123", "state": "device"}]
    isolated_service.ime_installed = lambda serial: True
    captured: list[tuple[str, ...]] = []

    def fake_run(*args, timeout=180):
        del timeout
        captured.append(args)
        return "ADBKeyBoard installed and enabled"

    isolated_service._run = fake_run
    result = isolated_service.install_ime("PHONE123")

    assert captured == [("install-ime", "--serial", "PHONE123")]
    assert result["installed"] is True
    assert result["serial"] == "PHONE123"


def test_install_ime_refuses_unauthorized_device_with_actionable_message(isolated_service):
    """未授权时要让用户回手机点允许，而不是只报状态码。"""
    isolated_service.devices = lambda: [{"serial": "PHONE123", "state": "unauthorized"}]
    isolated_service._run = lambda *args, timeout=180: pytest.fail("未授权时不得调用脚本")

    with pytest.raises(RuntimeError) as excinfo:
        isolated_service.install_ime("PHONE123")

    assert "始终允许" in str(excinfo.value)


def test_install_ime_refuses_missing_device(isolated_service):
    isolated_service.devices = lambda: []
    isolated_service._run = lambda *args, timeout=180: pytest.fail("设备不在时不得调用脚本")

    with pytest.raises(RuntimeError, match="手机未连接"):
        isolated_service.install_ime("GONE")


def test_adb_missing_error_points_to_web_wizard_not_command_line(isolated_service):
    """部署到 Windows 后没人替用户敲命令，报错必须指向网页按钮。"""
    with pytest.raises(RuntimeError) as excinfo:
        isolated_service._adb("devices")

    text = str(excinfo.value)
    assert "一键安装" in text
    assert "brew" not in text and "python3" not in text


def test_json_forwards_extended_publish_timeout(isolated_service):
    captured = []

    def fake_run(*args, timeout=180):
        captured.append((args, timeout))
        return '{"status":"ok"}'

    isolated_service._run = fake_run

    assert isolated_service._json("publish", timeout=360) == {"status": "ok"}
    assert captured == [(('publish',), 360)]


def test_unknown_adb_install_task_is_rejected(isolated_service):
    with pytest.raises(RuntimeError, match="找不到该安装任务"):
        isolated_service.adb_install_status("does-not-exist")


def test_setup_status_reports_ime_state_for_selected_serial(isolated_service, tmp_path):
    tools = tmp_path / "install" / "platform-tools"
    tools.mkdir(parents=True)
    (tools / "adb").write_bytes(b"\x7fELF")
    isolated_service.ime_installed = lambda serial: serial == "PHONE123"

    status = isolated_service.setup_status("PHONE123")

    assert status["ime_checked"] is True
    assert status["ime_installed"] is True
    assert status["ime_serial"] == "PHONE123"


def test_setup_status_survives_ime_probe_failure(isolated_service, tmp_path):
    """手机中途拔线不该让状态接口 500。"""
    tools = tmp_path / "install" / "platform-tools"
    tools.mkdir(parents=True)
    (tools / "adb").write_bytes(b"\x7fELF")

    def boom(serial):
        del serial
        raise RuntimeError("device offline")

    isolated_service.ime_installed = boom
    status = isolated_service.setup_status("PHONE123")

    assert status["adb_available"] is True
    assert status["ime_checked"] is False


def test_setup_android_script_covers_all_three_platforms():
    """Windows 地址必须在，否则最终部署形态没得下载。"""
    source = (VENDORED_DIR / "scripts" / "setup_android.py").read_text(encoding="utf-8")

    for system in ("darwin", "linux", "windows"):
        assert f"platform-tools-latest-{system}.zip" in source
    assert android_service_module.PLATFORM_TOOLS_URLS.keys() == {"Darwin", "Linux", "Windows"}


def test_service_urls_match_vendored_setup_script():
    """两处地址必须一致，避免以后只改一边。"""
    source = (VENDORED_DIR / "scripts" / "setup_android.py").read_text(encoding="utf-8")

    for url in android_service_module.PLATFORM_TOOLS_URLS.values():
        assert url in source, f"setup_android.py 缺少地址：{url}"


# --- 接口层：网页上点得到，才算「无需命令行」 -------------------------


class _FakeAndroidService:
    def __init__(self) -> None:
        self.ime_calls: list[str] = []
        self.install_starts = 0
        self.status_serial: str | None = None

    def setup_status(self, serial=None):
        self.status_serial = serial
        return {"adb_available": False, "adb_installable": True, "ime_serial": serial or ""}

    def start_adb_install(self):
        self.install_starts += 1
        return {"id": "task-1", "status": "running", "progress": 15, "message": "正在下载", "error": ""}

    def adb_install_status(self, task_id):
        if task_id != "task-1":
            raise RuntimeError("找不到该安装任务，请重新点击安装。")
        return {"id": "task-1", "status": "failed", "progress": 0, "error": "连不上谷歌下载服务器"}

    def install_ime(self, serial):
        self.ime_calls.append(serial)
        if serial == "UNAUTH":
            raise RuntimeError("手机尚未授权这台电脑，请勾选「始终允许」后重试。")
        return {"serial": serial, "installed": True, "message": "中文输入法已安装并启用"}


@pytest.fixture
def api(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.app.api.accounts import get_android_xhs_service
    from backend.app.core.database import Base, get_db
    from backend.app.main import app

    engine = create_engine(f"sqlite:///{tmp_path / 'c02.db'}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    fake = _FakeAndroidService()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_android_xhs_service] = lambda: fake
    client = TestClient(app)
    registered = client.post("/api/auth/register", json={"username": "c02op", "password": "secret123"})
    headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    try:
        yield client, headers, fake
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_android_xhs_service, None)


def test_setup_adb_endpoint_returns_long_task_for_frontend_polling(api):
    client, headers, fake = api

    response = client.post("/api/accounts/android/setup-adb", headers=headers)

    assert response.status_code == 200
    assert response.json()["id"] == "task-1"
    assert response.json()["status"] == "running"
    assert fake.install_starts == 1


def test_setup_adb_status_endpoint_exposes_readable_failure(api):
    client, headers, _ = api

    response = client.get("/api/accounts/android/setup-adb/task-1", headers=headers)

    assert response.status_code == 200
    assert response.json()["error"] == "连不上谷歌下载服务器"


def test_setup_adb_status_endpoint_404s_on_unknown_task(api):
    client, headers, _ = api

    assert client.get("/api/accounts/android/setup-adb/nope", headers=headers).status_code == 404


def test_install_ime_endpoint_forwards_requested_serial(api):
    client, headers, fake = api

    response = client.post("/api/accounts/android/install-ime", json={"serial": "PHONE123"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["installed"] is True
    assert fake.ime_calls == ["PHONE123"], "必须把前端选中的序列号原样传下去"


def test_install_ime_endpoint_surfaces_authorization_hint(api):
    client, headers, _ = api

    response = client.post("/api/accounts/android/install-ime", json={"serial": "UNAUTH"}, headers=headers)

    assert response.status_code == 400
    assert "始终允许" in response.json()["detail"]


def test_setup_status_endpoint_passes_serial_through_for_ime_check(api):
    client, headers, fake = api

    response = client.get("/api/accounts/android/setup-status", params={"serial": "PHONE123"}, headers=headers)

    assert response.status_code == 200
    assert fake.status_serial == "PHONE123"


def test_selectable_thumbnails_sorted_by_screen_position():
    """缩略图必须按屏幕位置排序（从上到下、从左到右），否则选图顺序会乱。
    用户实测过：草稿工坊定的顺序到手机上变了。"""
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    container = ET.SubElement(root, "node", {"scrollable": "true", "bounds": "[0,0][1080,2000]"})
    # 故意打乱顺序：第三行第二列在 XML 里排最前
    for text, bounds in [
        ("img3_2", "[540,1000][800,1300]"),   # 第3行第2列
        ("img1_1", "[0,100][270,370]"),        # 第1行第1列
        ("img2_2", "[540,500][810,770]"),      # 第2行第2列
        ("img1_2", "[270,100][540,370]"),      # 第1行第2列
        ("img2_1", "[0,500][270,770]"),        # 第2行第1列
        ("img3_1", "[0,1000][270,1300]"),      # 第3行第1列
    ]:
        ET.SubElement(container, "node", {
            "clickable": "true",
            "class": "android.widget.ImageView",
            "bounds": bounds,
            "content-desc": text,
        })

    result = module.selectable_thumbnails(root)

    descs = [node.attrib.get("content-desc", "") for node in result]
    assert descs == ["img1_1", "img1_2", "img2_1", "img2_2", "img3_1", "img3_2"], (
        f"缩略图应按屏幕位置排序，实际：{descs}"
    )


def test_selectable_thumbnails_supports_720px_three_column_gallery():
    """720px 屏幕的三列相册单格为 236px，不能继续用 250px 固定阈值。"""
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    container = ET.SubElement(root, "node", {"scrollable": "true", "bounds": "[0,252][720,1568]"})
    for index, bounds in enumerate((
        "[0,252][236,488]",
        "[242,252][478,488]",
        "[484,252][720,488]",
        "[0,494][236,730]",
    )):
        ET.SubElement(container, "node", {
            "clickable": "true",
            "class": "android.widget.ImageView",
            "bounds": bounds,
            "content-desc": f"image-{index + 1}",
        })
    ET.SubElement(container, "node", {
        "clickable": "true",
        "class": "android.widget.ImageView",
        "bounds": "[12,78][122,158]",
        "content-desc": "关闭",
    })

    result = module.selectable_thumbnails(root)

    assert [node.attrib["content-desc"] for node in result] == [
        "image-1", "image-2", "image-3", "image-4",
    ]


def test_product_search_control_supports_720px_screen():
    """商品搜索图标在 720px 屏幕的 x=660，不能用桌面宽屏坐标判断。"""
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    for text, bounds in (
        ("普通商品", "[24,194][184,250]"),
        ("店内爆品", "[184,194][344,250]"),
        ("最新上架", "[344,194][504,250]"),
        ("热门搜索", "[504,194][640,250]"),
    ):
        ET.SubElement(root, "node", {"text": text, "bounds": bounds})
    search_icon = ET.SubElement(root, "node", {
        "text": "",
        "class": "android.widget.ImageView",
        "clickable": "true",
        "bounds": "[660,202][700,242]",
    })

    assert module.product_search_control(root) is search_icon


def test_product_search_control_ignores_unrelated_icons():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {"text": "热门搜索", "bounds": "[504,194][640,250]"})
    ET.SubElement(root, "node", {
        "class": "android.widget.ImageView",
        "clickable": "true",
        "bounds": "[0,80][108,188]",
    })

    assert module.product_search_control(root) is None


def test_product_result_node_ignores_search_input_and_accepts_full_title():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {
        "text": "尹颜森林治愈系田园小碎花软毛牙刷",
        "class": "android.widget.EditText",
        "bounds": "[108,194][538,254]",
    })
    result = ET.SubElement(root, "node", {
        "text": "Y.senlin 尹颜森林治愈系田园小碎花软毛牙刷20孔宽头牙刷高效清洁情侣成人",
        "class": "android.widget.TextView",
        "bounds": "[312,346][664,386]",
    })

    assert module.product_result_node(root, "尹颜森林治愈系田园小碎花软毛牙刷") is result


def test_scroll_to_find_product_result_skips_search_input_and_scrolls(monkeypatch):
    """商品名在后续虚拟列表页时仍应命中，而不是返回搜索框。"""
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    keyword = "尹颜森林治愈系田园小碎花软毛牙刷"
    first = ET.Element("hierarchy")
    ET.SubElement(first, "node", {
        "scrollable": "true",
        "bounds": "[0,300][720,1500]",
    })
    ET.SubElement(first, "node", {
        "text": keyword,
        "class": "android.widget.EditText",
        "bounds": "[108,194][538,254]",
    })
    second = ET.Element("hierarchy")
    container = ET.SubElement(second, "node", {
        "scrollable": "true",
        "bounds": "[0,300][720,1500]",
    })
    result = ET.SubElement(container, "node", {
        "text": f"品牌 {keyword} 成人款",
        "class": "android.widget.TextView",
        "bounds": "[32,420][688,500]",
    })
    screens = iter([first, second])
    swipes = []
    monkeypatch.setattr(module, "dump_ui", lambda _serial: next(screens))
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: swipes.append(args))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    found, _ = module.scroll_to_find_product_result("SERIAL", keyword)

    assert found is result
    assert swipes, "商品结果不在首屏时必须滚动"


def test_clickable_ancestor_uses_actual_overlay_branch():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {
        "class": "android.view.ViewGroup",
        "clickable": "true",
        "bounds": "[32,266][688,474]",
    })
    overlay_card = ET.SubElement(root, "node", {
        "class": "android.view.ViewGroup",
        "clickable": "true",
        "bounds": "[32,322][688,530]",
    })
    inner = ET.SubElement(overlay_card, "node", {"clickable": "false"})
    result = ET.SubElement(inner, "node", {
        "text": "Y.senlin 尹颜森林治愈系田园小碎花软毛牙刷",
        "class": "android.widget.TextView",
        "bounds": "[312,346][664,386]",
    })

    assert module.clickable_ancestor(root, result) is overlay_card


def test_publish_page_detection_accepts_label_and_resource_id_variants():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    for attrs in (
        {"text": "发布", "clickable": "false"},
        {"content-desc": "发布笔记", "clickable": "true"},
        {"resource-id": "com.xingin.xhs:id/a5t", "clickable": "false"},
    ):
        root = ET.Element("hierarchy")
        ET.SubElement(root, "node", attrs)
        assert module._is_publish_page(root)


def test_selected_product_marker_accepts_new_ui_labels():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    marker = ET.SubElement(root, "node", {"text": "已选商品（1）"})
    assert module._selected_product_marker(root) is marker


def test_schedule_flow_finds_lower_advanced_options_and_direct_schedule_entry(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()

    compose = ET.Element("hierarchy")
    advanced = ET.SubElement(compose, "node", {
        "text": "高级选项", "content-desc": "高级选项", "bounds": "[0,1200][720,1300]",
    })
    menu = ET.Element("hierarchy")
    schedule = ET.SubElement(menu, "node", {
        "text": "定时发布", "content-desc": "定时发布", "bounds": "[0,1000][720,1100]",
    })
    picker = ET.Element("hierarchy")
    ET.SubElement(picker, "node", {"class": "android.widget.EditText", "hint": "日期", "bounds": "[0,0][100,100]"})
    ET.SubElement(picker, "node", {"class": "android.widget.EditText", "hint": "时间", "bounds": "[100,0][200,100]"})
    ET.SubElement(picker, "node", {"text": "确定", "clickable": "true", "bounds": "[600,0][720,100]"})
    returned = ET.Element("hierarchy")
    for label in ("商品", "文件", "引用笔记"):
        ET.SubElement(returned, "node", {"text": label})

    screens = iter((compose, menu, picker, picker, returned))
    taps = []
    monkeypatch.setattr(module, "scroll_to_find_node", lambda *args, **kwargs: advanced)
    monkeypatch.setattr(module, "dump_ui", lambda serial: next(screens))
    monkeypatch.setattr(module, "tap_node", lambda serial, node: taps.append(node.attrib.get("text")))
    monkeypatch.setattr(module, "_fill_edit_text", lambda *args: None)
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    module.schedule_on_phone("SERIAL", "2026-09-05T05:00")

    assert taps[:2] == ["高级选项", "定时发布"]
    assert "确定" in taps


def test_schedule_confirm_returns_from_component_sheet_to_compose(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    picker = ET.Element("hierarchy")
    ET.SubElement(picker, "node", {"class": "android.widget.EditText", "hint": "日期", "bounds": "[0,0][100,100]"})
    ET.SubElement(picker, "node", {"class": "android.widget.EditText", "hint": "时间", "bounds": "[100,0][200,100]"})
    ET.SubElement(picker, "node", {"text": "确定", "clickable": "true", "bounds": "[600,0][720,100]"})
    sheet = ET.Element("hierarchy")
    ET.SubElement(sheet, "node", {"text": "商品"})
    ET.SubElement(sheet, "node", {"text": "文件"})
    ET.SubElement(sheet, "node", {"text": "引用笔记"})
    ET.SubElement(sheet, "node", {"text": "活动"})
    ET.SubElement(sheet, "node", {"class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[32,1012][688,1496]"})
    compose = ET.Element("hierarchy")
    ET.SubElement(compose, "node", {"resource-id": "com.xingin.xhs:id/b5i", "hint": "添加标题"})

    entry = ET.Element("hierarchy")
    ET.SubElement(entry, "node", {"text": "高级选项", "clickable": "true", "bounds": "[0,0][720,100]"})
    menu = ET.Element("hierarchy")
    ET.SubElement(menu, "node", {"text": "定时发布", "clickable": "true", "bounds": "[0,0][720,100]"})
    screens = iter((entry, menu, picker, picker, sheet, compose))
    taps = []
    backs = []
    monkeypatch.setattr(module, "dump_ui", lambda _serial: next(screens))
    monkeypatch.setattr(module, "_fill_edit_text", lambda *args: None)
    monkeypatch.setattr(module, "scroll_to_find_node", lambda *args, **kwargs: entry)
    monkeypatch.setattr(module, "tap_node", lambda _serial, node: taps.append(node.attrib.get("text", "")))
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: backs.append(args) if args[-1] == "4" else None)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    module.schedule_on_phone("SERIAL", "2026-09-07T16:59")

    assert "确定" in taps
    assert backs, "确认后停在组件面板时必须返回正文编辑页"


def test_schedule_wheel_selector_uses_visible_semantic_value(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    wheel = ET.SubElement(root, "node", {
        "class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[0,100][200,500]",
    })
    row = ET.SubElement(wheel, "node", {
        "text": "9月5日 周六", "clickable": "true", "bounds": "[0,260][200,340]",
    })
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: pytest.fail("visible value should not swipe"))

    selected = module._select_schedule_wheel("SERIAL", root, "9月5日", 0, date_value=True)

    assert selected is row


def test_schedule_wheel_selector_accepts_partially_clipped_row():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    wheel = ET.SubElement(root, "node", {
        "class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[0,100][200,500]",
    })
    row = ET.SubElement(wheel, "node", {
        "text": "46", "clickable": "true", "bounds": "[0,460][200,540]",
    })

    selected = module._select_schedule_wheel("SERIAL", root, "46", 0)

    assert selected is row


def test_schedule_wheel_selector_does_not_swipe_when_matching_text_has_bad_y_bounds():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    wheel = ET.SubElement(root, "node", {
        "class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[400,100][600,500]",
    })
    ET.SubElement(wheel, "node", {
        "text": "46", "clickable": "true", "bounds": "[450,1068][550,1047]",
    })
    calls = []
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: calls.append(args))
        monkeypatch.setattr(module, "dump_ui", lambda _serial: root)
        with pytest.raises(SystemExit, match="找不到目标时间项"):
            module._select_schedule_wheel("SERIAL", root, "46", 0)
    finally:
        monkeypatch.undo()

    assert calls, "倒置 bounds 不能被点击，应该继续滚动"


def test_schedule_wheel_selector_orders_columns_by_horizontal_position():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    # Deliberately append minute, date, hour in unstable XML order.
    for bounds, value in (("[469,100][688,500]", "59"), ("[32,100][251,500]", "9月7日"), ("[251,100][469,500]", "16")):
        wheel = ET.SubElement(root, "node", {
            "class": "android.widget.ScrollView", "scrollable": "true", "bounds": bounds,
        })
        ET.SubElement(wheel, "node", {"text": value, "clickable": "true", "bounds": bounds})
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: pytest.fail("all targets are visible"))
        assert module._select_schedule_wheel("SERIAL", root, "9月7日", 0, date_value=True).attrib["text"] == "9月7日"
        assert module._select_schedule_wheel("SERIAL", root, "16", 1).attrib["text"] == "16"
        assert module._select_schedule_wheel("SERIAL", root, "59", 2).attrib["text"] == "59"
    finally:
        monkeypatch.undo()


def test_schedule_wheel_selector_returns_leaf_clickable_ancestor_explicitly():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    wheel = ET.SubElement(root, "node", {
        "class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[0,100][200,500]",
    })
    parent = ET.SubElement(wheel, "node", {
        "class": "android.view.ViewGroup", "clickable": "true", "bounds": "[0,260][200,340]",
    })
    row = ET.SubElement(parent, "node", {
        "text": "16", "class": "android.widget.TextView", "bounds": "[20,280][180,320]",
    })

    selected = module._select_schedule_wheel("SERIAL", root, "16", 0)

    assert selected is parent


def test_schedule_wheel_selector_falls_back_when_clickable_parent_bounds_inverted():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    wheel = ET.SubElement(root, "node", {
        "class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[0,100][200,500]",
    })
    parent = ET.SubElement(wheel, "node", {
        "class": "android.view.ViewGroup", "clickable": "true", "bounds": "[0,1068][200,108]",
    })
    row = ET.SubElement(parent, "node", {
        "text": "16", "class": "android.widget.TextView", "bounds": "[20,280][180,320]",
    })

    selected = module._select_schedule_wheel("SERIAL", root, "16", 0)

    assert selected is row


def test_schedule_wheel_selector_reorders_wheels_after_each_scroll(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()

    def screen(order, values):
        root = ET.Element("hierarchy")
        for bounds, value in zip(order, values):
            wheel = ET.SubElement(root, "node", {
                "class": "android.widget.ScrollView", "scrollable": "true", "bounds": bounds,
            })
            ET.SubElement(wheel, "node", {
                "text": value, "clickable": "true", "bounds": bounds,
            })
        return root

    # Initial XML order: date/hour/minute. After a scroll, XML order changes
    # to minute/date/hour; horizontal ordering must still select the hour.
    screens = iter([
        screen(("[32,100][251,500]", "[251,100][469,500]", "[469,100][688,500]"), ("x", "15", "00")),
        screen(("[469,100][688,500]", "[32,100][251,500]", "[251,100][469,500]"), ("00", "x", "16")),
    ])
    monkeypatch.setattr(module, "dump_ui", lambda _serial: next(screens))
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: None)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    selected = module._select_schedule_wheel("SERIAL", screens.__next__(), "16", 1)

    assert selected.attrib["text"] == "16"


def test_publish_page_detection_accepts_scrolled_compose_toolbar():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    for label in ("商品", "引用笔记", "文件", "活动", "添加"):
        ET.SubElement(root, "node", {"text": label})
    assert module._is_publish_page(root)


def test_scheduled_publish_button_accepts_phone_final_action_label():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    final_action = ET.SubElement(root, "node", {
        "text": "定时发布",
        "class": "android.widget.Button",
        "clickable": "true",
        "bounds": "[331,1434][688,1514]",
    })

    assert module._find_publish_button(root, scheduled=True) is final_action
    assert module._find_publish_button(root, scheduled=False) is None


def test_compose_page_detection_accepts_single_image_auto_editor():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {"resource-id": "com.xingin.xhs:id/b5i", "class": "android.widget.EditText"})
    assert module._is_compose_page(root)


def test_component_sheet_is_not_mistaken_for_compose_page():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {
        "class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[32,1012][688,1496]",
    })
    for label in ("商品", "文件", "引用笔记", "活动"):
        ET.SubElement(root, "node", {"text": label})

    assert module._is_component_sheet(root)


def test_compose_page_with_editor_fields_is_not_component_sheet():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {
        "class": "android.widget.ScrollView", "scrollable": "true", "bounds": "[32,500][688,1496]",
    })
    ET.SubElement(root, "node", {"resource-id": "com.xingin.xhs:id/b5i", "hint": "添加标题"})
    for label in ("商品", "文件", "引用笔记", "活动"):
        ET.SubElement(root, "node", {"text": label})

    assert module._is_compose_page(root)
    assert not module._is_component_sheet(root)


def test_topic_candidate_ignores_plain_editor_text():
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {
        "text": "牙刷推荐",
        "class": "android.widget.EditText",
    })
    suggestion = ET.SubElement(root, "node", {
        "text": "牙刷推荐",
        "class": "android.widget.TextView",
    })

    assert module.topic_candidate(root, "牙刷推荐") is suggestion


def test_topics_from_file_normalizes_deduplicates_and_limits(tmp_path):
    import json

    module = _load_android_script_module()
    topics_file = tmp_path / "topics.json"
    topics_file.write_text(
        json.dumps(["#牙刷", "牙刷", " 口腔护理 ", *[f"话题{i}" for i in range(20)]], ensure_ascii=False),
        encoding="utf-8",
    )

    result = module.topics_from_file(str(topics_file))

    assert result[:2] == ["牙刷", "口腔护理"]
    assert len(result) == 10


def test_attach_topics_selects_suggestion_instead_of_leaving_plain_text(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()

    def ui(*texts, body=None, done=False):
        root = ET.Element("hierarchy")
        if body is not None:
            ET.SubElement(root, "node", {
                "text": body,
                "hint": "添加正文",
                "class": "android.widget.EditText",
                "bounds": "[0,410][720,742]",
            })
        for index, text in enumerate(texts):
            ET.SubElement(root, "node", {
                "text": text,
                "class": "android.widget.TextView",
                "bounds": f"[68,{800 + index * 72}][300,{860 + index * 72}]",
            })
        if done:
            ET.SubElement(root, "node", {
                "text": "完成",
                "class": "android.widget.TextView",
                "clickable": "true",
                "bounds": "[634,630][710,702]",
            })
        return root

    screens = iter([
        ui("话题"),
        ui("话题"),
        ui("牙刷推荐", body="#"),
        ui(body="#牙刷推荐 ", done=True),
    ])
    taps = []
    cursor_moves = []
    monkeypatch.setattr(module, "dump_ui", lambda _serial: next(screens))
    monkeypatch.setattr(module, "current_ime", lambda _serial: "original/.Ime")
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "tap_node", lambda _serial, node: taps.append(node.attrib.get("text", "")))
    monkeypatch.setattr(module, "broadcast_unicode", lambda *_args: pytest.fail("默认候选存在时不应输入普通文本"))
    monkeypatch.setattr(module, "move_body_cursor_to_end", lambda serial: cursor_moves.append(serial))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    result = module.attach_topics("SERIAL", ["牙刷推荐"])

    assert taps == ["话题", "牙刷推荐", "完成"]
    assert cursor_moves == ["SERIAL"]
    assert result == {"bound": ["牙刷推荐"], "skipped": []}


def test_attach_topics_positions_long_body_cursor_only_once(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()

    def node(text):
        return ET.Element("node", {
            "text": text,
            "class": "android.widget.TextView",
            "bounds": "[68,800][300,860]",
        })

    initial = ET.Element("hierarchy")
    first_suggestion = ET.Element("hierarchy")
    first_suggestion.append(node("牙刷"))
    second_suggestion = ET.Element("hierarchy")
    second_suggestion.append(node("口腔护理"))
    screens = iter([initial, first_suggestion, second_suggestion])
    cursor_moves = []
    monkeypatch.setattr(module, "dump_ui", lambda _serial: next(screens))
    monkeypatch.setattr(module, "current_ime", lambda _serial: "original/.Ime")
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "tap_node", lambda *_args: None)
    monkeypatch.setattr(module, "move_body_cursor_to_end", lambda serial: cursor_moves.append(serial))
    monkeypatch.setattr(
        module,
        "wait_node",
        lambda _serial, *, texts, **_kwargs: node("话题" if texts == ("话题",) else "完成"),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    result = module.attach_topics("SERIAL", ["牙刷", "口腔护理"])

    assert result == {"bound": ["牙刷", "口腔护理"], "skipped": []}
    assert cursor_moves == ["SERIAL"]


def test_attach_topics_skips_missing_exact_candidate_and_continues(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()

    def ui(*texts, body=None, done=False):
        root = ET.Element("hierarchy")
        if body is not None:
            ET.SubElement(root, "node", {
                "text": body,
                "hint": "添加正文",
                "class": "android.widget.EditText",
                "bounds": "[0,410][720,742]",
            })
        for index, text in enumerate(texts):
            ET.SubElement(root, "node", {
                "text": text,
                "class": "android.widget.TextView",
                "bounds": f"[68,{800 + index * 72}][300,{860 + index * 72}]",
            })
        if done:
            ET.SubElement(root, "node", {
                "text": "完成",
                "class": "android.widget.TextView",
                "clickable": "true",
                "bounds": "[634,630][710,702]",
            })
        return root

    screens = iter([
        ui("话题"),
        ui("话题"),
        ui("其他候选", body="#"),
        ui("话题"),
        ui("话题"),
        ui("牙刷", body="#"),
        ui(body="#牙刷 ", done=True),
    ])
    monkeypatch.setattr(module, "dump_ui", lambda _serial: next(screens))
    monkeypatch.setattr(module, "current_ime", lambda _serial: "original/.Ime")
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "tap_node", lambda *_args: None)
    monkeypatch.setattr(module, "broadcast_unicode", lambda *_args: None)
    monkeypatch.setattr(module, "move_body_cursor_to_end", lambda _serial: None)
    monkeypatch.setattr(
        module,
        "wait_topic_candidate",
        lambda _serial, _topic: (None, ui("仍非精确候选", body="#不存在话题")),
    )
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    result = module.attach_topics("SERIAL", ["不存在话题", "牙刷"])

    assert result == {"bound": ["牙刷"], "skipped": ["不存在话题"]}


def test_move_body_cursor_to_end_uses_android_end_key(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    editor = ET.Element("hierarchy")
    body = ET.SubElement(editor, "node", {
        "text": "很长的正文，当前光标可能在中间",
        "hint": "添加正文",
        "class": "android.widget.EditText",
        "bounds": "[0,470][720,802]",
    })
    expanded = ET.Element("hierarchy")
    done = ET.SubElement(expanded, "node", {
        "text": "完成",
        "class": "android.widget.TextView",
        "clickable": "true",
        "bounds": "[634,630][710,702]",
    })
    screens = iter([editor, expanded])
    taps = []
    adb_calls = []
    monkeypatch.setattr(module, "dump_ui", lambda _serial: next(screens))
    monkeypatch.setattr(module, "tap_node", lambda _serial, node: taps.append(node))
    monkeypatch.setattr(module, "run_adb", lambda *args, **kwargs: adb_calls.append(args))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    module.move_body_cursor_to_end("SERIAL")

    assert taps == [body, done]
    assert any(call[-120:] == ("20",) * 120 for call in adb_calls)
    assert any(call[-2:] == ("keyevent", "123") for call in adb_calls)


def test_android_topics_prefers_reviewed_topics_and_falls_back_to_draft_tags():
    import json
    from types import SimpleNamespace

    from backend.app.api.publish import _android_topics

    reviewed = SimpleNamespace(publish_options=json.dumps({"topics": ["#牙刷", "口腔护理"]}))
    fallback = SimpleNamespace(publish_options=json.dumps({
        "draft_tags": [
            {"id": "topic-1", "name": "牙刷推荐"},
            {"id": "topic-2", "name": "软毛牙刷"},
            {"id": "", "name": "AI自造但不存在的话题"},
        ],
    }))

    assert _android_topics(reviewed) == ["牙刷", "口腔护理"]
    assert _android_topics(fallback) == ["牙刷推荐", "软毛牙刷"]


def test_android_setup_endpoints_require_authentication(api):
    client, _, _ = api

    assert client.post("/api/accounts/android/setup-adb").status_code == 401
    assert client.post("/api/accounts/android/install-ime", json={"serial": "X"}).status_code == 401


# --- 相册滚动查找（用户实测：相册就在下面，脚本没滚动就报找不到）---

def _load_android_script_module():
    """把发布脚本作为模块载入，以便直接测其纯函数。"""
    import importlib.util

    path = VENDORED_DIR / "scripts" / "xhs_android.py"
    spec = importlib.util.spec_from_file_location("xhs_android_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ui(rows, *, scrollable_bounds="[0,300][1080,2100]"):
    """构造一个含可滚动容器的 UI 树，容器内只有 rows 里的文本。"""
    import xml.etree.ElementTree as ET

    root = ET.Element("hierarchy")
    container = ET.SubElement(root, "node", {"scrollable": "true", "bounds": scrollable_bounds})
    for index, text in enumerate(rows):
        ET.SubElement(
            container, "node",
            {"text": text, "bounds": f"[0,{400 + index * 200}][1080,{560 + index * 200}]"},
        )
    return root


def test_scroll_to_find_node_returns_target_already_on_screen(monkeypatch):
    module = _load_android_script_module()
    monkeypatch.setattr(module, "dump_ui", lambda serial: _ui(["全部", "CodexXHS"]))
    swipes = []
    monkeypatch.setattr(module, "run_adb", lambda *a, **k: swipes.append(a))

    found = module.scroll_to_find_node("SERIAL", texts=("CodexXHS",))

    assert found is not None
    assert swipes == [], "已在屏幕上时不应滚动"


def test_scroll_to_find_node_scrolls_until_target_appears(monkeypatch):
    module = _load_android_script_module()
    screens = [
        _ui(["全部", "最近", "相机"]),
        _ui(["微信", "下载"]),
        _ui(["截屏", "CodexXHS"]),
    ]
    calls = {"n": 0}

    def fake_dump(serial):
        index = min(calls["n"], len(screens) - 1)
        calls["n"] += 1
        return screens[index]

    swipes = []
    monkeypatch.setattr(module, "dump_ui", fake_dump)
    monkeypatch.setattr(module, "run_adb", lambda *a, **k: swipes.append(a))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    found = module.scroll_to_find_node("SERIAL", texts=("CodexXHS",))

    assert found is not None, "目标在第三屏，必须滚动后才找到"
    assert len(swipes) >= 1


def test_scroll_to_find_node_stops_when_list_stops_changing(monkeypatch):
    """到底了就停，不要白滚满 max_scrolls 次。"""
    module = _load_android_script_module()
    monkeypatch.setattr(module, "dump_ui", lambda serial: _ui(["全部", "相机"]))
    swipes = []
    monkeypatch.setattr(module, "run_adb", lambda *a, **k: swipes.append(a))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    found = module.scroll_to_find_node("SERIAL", texts=("CodexXHS",), max_scrolls=12)

    assert found is None
    assert len(swipes) <= 2, f"列表不再变化应尽早停止，实际滚了 {len(swipes)} 次"


def test_scroll_swipe_coordinates_derive_from_container_bounds(monkeypatch):
    """坐标必须由容器边界算出，不能写死——否则换屏幕尺寸就失效。"""
    module = _load_android_script_module()
    screens = [_ui(["A"], scrollable_bounds="[0,500][720,1400]"), _ui(["CodexXHS"])]
    calls = {"n": 0}

    def fake_dump(serial):
        index = min(calls["n"], len(screens) - 1)
        calls["n"] += 1
        return screens[index]

    swipes = []
    monkeypatch.setattr(module, "dump_ui", fake_dump)
    monkeypatch.setattr(module, "run_adb", lambda *a, **k: swipes.append([str(x) for x in a]))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    module.scroll_to_find_node("SERIAL", texts=("CodexXHS",))

    assert swipes, "应发生过滚动"
    args = swipes[0]
    x = int(args[args.index("swipe") + 1])
    assert x == 360, f"横坐标应是容器中线 360，实际 {x}"


def test_scrollable_node_picks_tallest_container(monkeypatch):
    import xml.etree.ElementTree as ET

    module = _load_android_script_module()
    root = ET.Element("hierarchy")
    ET.SubElement(root, "node", {"scrollable": "true", "bounds": "[0,0][1080,200]"})
    tall = ET.SubElement(root, "node", {"scrollable": "true", "bounds": "[0,300][1080,2000]"})

    assert module._scrollable_node(root) is tall
