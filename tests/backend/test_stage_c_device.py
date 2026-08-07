"""阶段 C：设备助手。覆盖安卓脚本的仓库内自持（BUG-03）与网页内自助连机（C-02）。"""

import urllib.error
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
