from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.app.core.config import get_settings
from backend.app.core.time import shanghai_now
from backend.app.services.asset_downloader import download_asset_to_local


XHS_PACKAGE = "com.xingin.xhs"
DEFAULT_ANDROID_USER = "0"

# 中文输入法。adb 原生 `input text` 打不了中文，填标题正文必须靠它。
IME_PACKAGE = "com.android.adbkeyboard"

# 与 scripts/android/scripts/setup_android.py 保持一致的官方下载地址。
PLATFORM_TOOLS_URLS = {
    "Darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    "Linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "Windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
}

# Windows 上可执行文件叫 adb.exe。不按 sys.platform 硬分支，两个名字都试，
# 这样 macOS 行为完全不变，Windows 上只是多一条候选。
ADB_EXECUTABLE_NAMES = ("adb", "adb.exe")

ADB_INSTALL_DIR = Path.home() / ".local" / "share" / "xhs-publishing"


@dataclass
class AdbInstallTask:
    """一键安装 adb 的长任务状态。下载几十 MB，同步阻塞会超时。"""

    id: str
    status: str = "pending"  # pending | running | success | failed
    progress: int = 0
    message: str = "准备中"
    error: str = ""
    adb_path: str = ""
    already_installed: bool = False
    updated_at: str = field(default_factory=lambda: shanghai_now().isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _AdbInstallRegistry:
    """进程内任务表。服务按请求新建实例，所以状态必须挂在模块级。"""

    # 超过这么久没有任何进度更新就认定任务已死。否则线程被杀或进程假死后
    # 任务永远停在 pending，安装按钮再也点不动——而部署后用户没有命令行可用。
    STALE_SECONDS = 600.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, AdbInstallTask] = {}
        self._touched: dict[str, float] = {}
        self._active_id: str | None = None

    def active(self) -> AdbInstallTask | None:
        with self._lock:
            if not self._active_id:
                return None
            task = self._tasks.get(self._active_id)
            if not task or task.status not in {"pending", "running"}:
                return None
            if time.monotonic() - self._touched.get(task.id, 0.0) > self.STALE_SECONDS:
                task.status = "failed"
                task.error = "安装任务超时中断，请重新点击安装。"
                task.message = "安装失败"
                return None
            return task

    def create(self) -> AdbInstallTask:
        with self._lock:
            task = AdbInstallTask(id=uuid.uuid4().hex)
            self._tasks[task.id] = task
            self._touched[task.id] = time.monotonic()
            self._active_id = task.id
            return task

    def get(self, task_id: str) -> AdbInstallTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **changes: Any) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            for key, value in changes.items():
                setattr(task, key, value)
            task.updated_at = shanghai_now().isoformat()
            self._touched[task_id] = time.monotonic()

    def reset(self) -> None:
        """仅供测试隔离使用。"""
        with self._lock:
            self._tasks.clear()
            self._touched.clear()
            self._active_id = None


adb_install_registry = _AdbInstallRegistry()


class _AndroidPublishLockRegistry:
    """Serialize UI publishing per physical phone, while other phones run independently."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def _lock_for(self, serial: str) -> threading.Lock:
        key = str(serial or "").strip()
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    @contextmanager
    def acquire(self, serial: str):
        lock = self._lock_for(serial)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


android_publish_lock_registry = _AndroidPublishLockRegistry()


@contextmanager
def android_device_publish_lock(serial: str):
    """Queue Android publish calls that share one physical device serial."""
    with android_publish_lock_registry.acquire(serial):
        yield


def _readable_download_error(exc: BaseException) -> str:
    """把原始异常翻译成用户能看懂的原因。部署后没人替用户读 traceback。"""
    if isinstance(exc, zipfile.BadZipFile):
        return "下载的安装包已损坏（可能是下载中断或被网络代理篡改），请重试一次。"
    if isinstance(exc, urllib.error.HTTPError):
        return f"谷歌下载服务器返回错误 {exc.code}，通常是临时故障，请稍后重试。"
    if isinstance(exc, urllib.error.URLError):
        return (
            "连不上谷歌下载服务器（dl.google.com）。请检查这台电脑能否上网，"
            "国内网络通常需要先开启代理或 VPN 再重试。"
        )
    if isinstance(exc, TimeoutError):
        return "下载超时。网络太慢或被拦截，请检查网络后重试。"
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) == 28:
            return "磁盘空间不足，无法保存安装包。清理磁盘后重试。"
        if getattr(exc, "errno", None) == 13:
            return "没有写入权限，无法安装到目标目录。请用有权限的账户重试。"
        return f"写入文件失败：{exc.strerror or exc}"
    return f"安装失败：{exc}"


class AndroidXhsService:
    """Thin, local-only wrapper around the reviewed Android USB helper."""

    def __init__(self) -> None:
        configured = os.environ.get("XHS_ANDROID_SCRIPT", "").strip()
        repo_root = Path(__file__).resolve().parents[3]
        candidates = [
            Path(configured).expanduser() if configured else None,
            # 仓库内副本优先。此前只有 ~/.codex 那份，换电脑或交付后该功能会静默失效（BUG-03）。
            repo_root / "scripts" / "android" / "scripts" / "xhs_android.py",
            Path.home() / ".codex" / "skills" / "xhs-publishing-workflow" / "scripts" / "xhs_android.py",
            repo_root / "scripts" / "xhs_android.py",
        ]
        fallback = candidates[1]
        self.script = next((path.resolve() for path in candidates if path and path.is_file()), fallback)

    def _install_dir(self) -> Path:
        configured = os.environ.get("XHS_ADB_INSTALL_DIR", "").strip()
        return (Path(configured) if configured else ADB_INSTALL_DIR).expanduser()

    def _adb_path(self) -> str | None:
        configured = os.environ.get("XHS_ADB", "").strip()
        local_tools = self._install_dir() / "platform-tools"
        candidates: list[str | None] = [
            configured or None,
            shutil.which("adb"),
            # Windows 上装好了也找不到，因为可执行文件是 adb.exe。两个名字都列。
            *[str(local_tools / name) for name in ADB_EXECUTABLE_NAMES],
        ]
        return next(
            (
                str(Path(path).expanduser().resolve())
                for path in candidates
                if path and Path(path).expanduser().is_file()
            ),
            None,
        )

    def setup_status(self, serial: str | None = None) -> dict[str, Any]:
        adb = self._adb_path()
        active = adb_install_registry.active()
        status: dict[str, Any] = {
            "adb_available": bool(adb),
            "adb_path": adb or "",
            "script_available": self.script.is_file(),
            "script_path": str(self.script),
            "ready_for_binding": bool(adb),
            "ready_for_publishing": bool(adb and self.script.is_file()),
            "platform": platform.system(),
            "adb_installable": platform.system() in PLATFORM_TOOLS_URLS,
            "adb_install_task_id": active.id if active else "",
            "ime_serial": serial or "",
            "ime_installed": False,
            "ime_checked": False,
        }
        if adb and serial:
            try:
                status["ime_installed"] = self.ime_installed(serial)
                status["ime_checked"] = True
            except Exception:
                # 手机可能刚拔线或未授权。检测失败不该让整个状态接口 500。
                status["ime_checked"] = False
        return status

    def ime_installed(self, serial: str) -> bool:
        packages = self._adb("shell", "pm", "list", "packages", IME_PACKAGE, serial=serial).stdout
        return f"package:{IME_PACKAGE}" in packages

    def install_ime(self, serial: str) -> dict[str, Any]:
        """装 ADBKeyboard 到指定手机。只调 xhs_android.py 的 install-ime 子命令。"""
        states = {item["serial"]: item["state"] for item in self.devices()}
        if serial not in states:
            raise RuntimeError(f"手机未连接：{serial}。请插好 USB 线后重新检测。")
        if states[serial] != "device":
            raise RuntimeError(
                f"手机尚未授权这台电脑（{states[serial]}）。请解锁手机，在弹窗里勾选"
                "「始终允许」并点确定，然后重新检测。"
            )
        output = self._run("install-ime", "--serial", serial, timeout=180)
        return {
            "serial": serial,
            "installed": self.ime_installed(serial),
            "message": output.strip() or "中文输入法已安装并启用",
        }

    # --- 一键安装 adb ---------------------------------------------------

    def _download_platform_tools(self, url: str, archive: Path) -> None:
        """独立成方法，便于测试 mock 掉——测试里绝不真的下载几十 MB。"""
        urllib.request.urlretrieve(url, archive)

    def _extract_adb(self, archive: Path, temp_dir: Path, target: Path) -> Path:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temp_dir)
        extracted = temp_dir / "platform-tools"
        found = next((extracted / name for name in ADB_EXECUTABLE_NAMES if (extracted / name).is_file()), None)
        if found is None:
            raise RuntimeError("下载的安装包里没有 adb，可能不是官方文件。请重试。")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extracted), str(target))
        installed = target / found.name
        if os.name != "nt":
            installed.chmod(0o755)
        return installed

    def install_adb_sync(self, task_id: str) -> None:
        """长任务主体。在后台线程里跑，全程只更新任务状态，不抛异常给调用方。"""
        report = lambda progress, message: adb_install_registry.update(  # noqa: E731
            task_id, status="running", progress=progress, message=message
        )
        existing = self._adb_path()
        if existing:
            adb_install_registry.update(
                task_id,
                status="success",
                progress=100,
                message="ADB 已安装，无需重复下载",
                adb_path=existing,
                already_installed=True,
            )
            return
        system = platform.system()
        url = PLATFORM_TOOLS_URLS.get(system)
        if not url:
            adb_install_registry.update(
                task_id,
                status="failed",
                progress=0,
                message="安装失败",
                error=f"不支持的操作系统：{system}。请手动安装 Android Platform Tools。",
            )
            return
        try:
            report(5, "正在连接谷歌官方下载服务器")
            with tempfile.TemporaryDirectory(prefix="xhs-platform-tools-") as raw_temp:
                temp_dir = Path(raw_temp)
                archive = temp_dir / "platform-tools.zip"
                report(15, "正在下载 Android Platform Tools（约 15 MB）")
                self._download_platform_tools(url, archive)
                report(70, "下载完成，正在解压安装")
                installed = self._extract_adb(archive, temp_dir, self._install_dir() / "platform-tools")
            report(90, "正在校验安装结果")
            resolved = self._adb_path() or str(installed)
            adb_install_registry.update(
                task_id,
                status="success",
                progress=100,
                message="ADB 安装完成，可以继续下一步",
                adb_path=resolved,
                error="",
            )
        except BaseException as exc:  # noqa: BLE001 - 后台线程必须兜住一切
            adb_install_registry.update(
                task_id,
                status="failed",
                progress=0,
                message="安装失败",
                error=_readable_download_error(exc),
            )

    def start_adb_install(self, spawn: Callable[[Callable[[], None]], None] | None = None) -> dict[str, Any]:
        """已安装直接返回成功；已有任务在跑就复用，重复点不会重复下载。"""
        existing = self._adb_path()
        if existing:
            task = adb_install_registry.create()
            adb_install_registry.update(
                task.id,
                status="success",
                progress=100,
                message="ADB 已安装，无需重复下载",
                adb_path=existing,
                already_installed=True,
            )
            return self.adb_install_status(task.id)
        active = adb_install_registry.active()
        if active:
            return active.to_dict()
        task = adb_install_registry.create()
        runner = lambda: self.install_adb_sync(task.id)  # noqa: E731
        if spawn is not None:
            spawn(runner)
        else:
            threading.Thread(target=runner, name=f"adb-install-{task.id}", daemon=True).start()
        return adb_install_registry.get(task.id).to_dict() if adb_install_registry.get(task.id) else task.to_dict()

    def adb_install_status(self, task_id: str) -> dict[str, Any]:
        task = adb_install_registry.get(task_id)
        if not task:
            raise RuntimeError("找不到该安装任务，请重新点击安装。")
        return task.to_dict()

    def _adb(self, *args: str, serial: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        adb = self._adb_path()
        if not adb:
            raise RuntimeError(
                "尚未安装 Android Platform Tools（adb）。请在网页「绑定安卓手机」向导第 1 步点击"
                "「一键安装 ADB」，无需使用命令行。"
            )
        command = [adb]
        if serial:
            command.extend(["-s", serial])
        command.extend(args)
        return subprocess.run(command, check=check, text=True, capture_output=True, timeout=30, encoding="utf-8")

    def _getprop(self, serial: str, name: str) -> str:
        return self._adb("shell", "getprop", name, serial=serial).stdout.strip()

    def _run(
        self,
        *args: str,
        timeout: int = 180,
        progress_file: str | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        if not self.script.is_file():
            raise RuntimeError(
                f"安卓发布脚本不可用：{self.script}。请安装 xhs-publishing-workflow，"
                "或设置 XHS_ANDROID_SCRIPT 指向 scripts/xhs_android.py。"
            )
        command = [sys.executable, str(self.script), *args]
        # Keep stderr out of a PIPE while scanning.  The Android script emits
        # diagnostic lines for every ADB/UI operation; if that pipe fills,
        # the child can block before it gets a chance to update its checkpoint.
        # A temporary file preserves diagnostics without creating backpressure
        # on the long-running scan process.
        with tempfile.TemporaryFile(mode="w+b") as stderr_file:
            process = subprocess.Popen(
                command, text=True, stdout=subprocess.PIPE, stderr=stderr_file,
                encoding="utf-8",
            )
            started = time.monotonic()
            last_read = 0.0
            while process.poll() is None:
                if progress and progress_file and time.monotonic() - last_read >= 1:
                    last_read = time.monotonic()
                    try:
                        value = json.loads(Path(progress_file).read_text(encoding="utf-8"))
                        if isinstance(value, dict):
                            progress(value)
                    except (OSError, json.JSONDecodeError):
                        pass
                if time.monotonic() - started >= timeout:
                    process.kill()
                    stdout, _ = process.communicate()
                    stderr_file.seek(0)
                    stderr = stderr_file.read().decode("utf-8", errors="replace")
                    raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
                time.sleep(0.2)
            stdout, _ = process.communicate()
            stderr_file.seek(0)
            stderr = stderr_file.read().decode("utf-8", errors="replace")
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        # 脚本的 stderr 是诊断日志（选图顺序等），无论成功失败都转发到后端控制台。
        if result.stderr:
            sys.stderr.write(result.stderr + "\n")
            sys.stderr.flush()
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "安卓设备命令失败") or "安卓设备命令失败")
        return (result.stdout or "").strip()

    def _json(self, *args: str, timeout: int = 180) -> Any:
        output = self._run(*args, timeout=timeout)
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("安卓设备返回格式异常") from exc

    def devices(self) -> list[dict[str, str]]:
        lines = self._adb("devices", "-l").stdout.splitlines()[1:]
        devices: list[dict[str, str]] = []
        for line in lines:
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

    def android_users(self, serial: str) -> list[dict[str, str]]:
        """List Android user/profile spaces available on a connected device."""
        output = self._adb("shell", "pm", "list", "users", serial=serial).stdout
        users: list[dict[str, str]] = []
        for match in re.finditer(r"UserInfo\{(\d+):([^:}]+)(?::[^}]*)?\}", output):
            user_id, label = match.groups()
            users.append({"id": user_id, "name": label.strip() or f"用户 {user_id}"})
        if not users:
            users.append({"id": DEFAULT_ANDROID_USER, "name": "主空间"})
        return users

    def doctor(self, serial: str, android_user: str = DEFAULT_ANDROID_USER) -> dict[str, Any]:
        states = {item["serial"]: item["state"] for item in self.devices()}
        if serial not in states:
            raise RuntimeError(f"手机未连接：{serial}")
        if states[serial] != "device":
            raise RuntimeError(f"手机尚未授权这台电脑：{serial}（{states[serial]}）")
        android_user = str(android_user or DEFAULT_ANDROID_USER)
        packages = self._adb("shell", "pm", "list", "packages", "--user", android_user, XHS_PACKAGE, serial=serial).stdout
        package_dump = self._adb("shell", "dumpsys", "package", XHS_PACKAGE, serial=serial).stdout
        version_name = ""
        for line in package_dump.splitlines():
            if "versionName=" in line:
                version_name = line.split("versionName=", 1)[1].strip()
                break
        return {
            "serial": serial,
            "android_user": android_user,
            "manufacturer": self._getprop(serial, "ro.product.manufacturer"),
            "model": self._getprop(serial, "ro.product.model"),
            "android": self._getprop(serial, "ro.build.version.release"),
            "sdk": self._getprop(serial, "ro.build.version.sdk"),
            "xiaohongshu_installed": f"package:{XHS_PACKAGE}" in packages,
            "xiaohongshu_version": version_name,
            "default_ime": self._adb("shell", "settings", "get", "secure", "default_input_method", serial=serial).stdout.strip(),
        }

    def scan_profile(
        self,
        serial: str,
        android_user: str = DEFAULT_ANDROID_USER,
        max_scrolls: int = 260,
        max_notes: int = 500,
        resume: bool = True,
        identity_only: bool = False,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        android_user = str(android_user or DEFAULT_ANDROID_USER)
        checkpoint_path = self._calibration_path(serial, android_user).with_name(
            f"{self._calibration_path(serial, android_user).stem}-scan.json"
        )
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        if not resume and not identity_only:
            checkpoint_path.unlink(missing_ok=True)
        resume_scroll_count = 0
        if resume and not identity_only and checkpoint_path.is_file():
            try:
                resume_scroll_count = max(0, int(json.loads(checkpoint_path.read_text(encoding="utf-8")).get("scroll_count", 0) or 0))
            except (OSError, ValueError, json.JSONDecodeError):
                resume_scroll_count = 0
        command_args = [
            "scan-profile", "--serial", serial, "--android-user", android_user,
            "--max-scrolls", str(max(0, min(max_scrolls, 400))),
            "--max-notes", str(max(1, min(max_notes, 1000))),
            "--checkpoint-file", "" if identity_only else str(checkpoint_path),
            "--resume-scroll-count", str(resume_scroll_count),
        ]
        if identity_only:
            command_args.append("--identity-only")
        try:
            output = self._run(
                *command_args,
                timeout=600,
                progress_file=None if identity_only else str(checkpoint_path),
                progress=progress,
            )
        except subprocess.TimeoutExpired as exc:
            if not identity_only:
                try:
                    partial = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    if isinstance(partial, dict) and partial.get("notes"):
                        partial["status"] = "scanned"
                        partial["end_reason"] = "timeout_partial"
                        partial["scan_error"] = f"手机扫描超过 10 分钟，已保留已读取内容：{exc}"
                        return partial
                except (OSError, json.JSONDecodeError):
                    pass
            raise RuntimeError("手机内容读取超时（超过 10 分钟），请确认手机保持亮屏并停留在小红书主页") from exc
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError("安卓设备返回格式异常") from exc
        if not isinstance(payload, dict) or payload.get("status") != "scanned":
            raise RuntimeError("手机端账号内容读取失败")
        if not identity_only:
            checkpoint_path.unlink(missing_ok=True)
        return payload

    def _calibration_path(self, serial: str, android_user: str = DEFAULT_ANDROID_USER) -> Path:
        safe_serial = "".join(char for char in serial if char.isalnum() or char in "-_")
        safe_user = "".join(char for char in str(android_user or DEFAULT_ANDROID_USER) if char.isalnum() or char in "-_")
        return Path(get_settings().storage_dir) / "android" / "device-profiles" / f"{safe_serial}-u{safe_user}.json"

    def ensure_calibrated(self, serial: str, device: dict[str, Any], android_user: str = DEFAULT_ANDROID_USER) -> dict[str, Any]:
        """Run a safe first-use UI walkthrough for each device/app version."""
        android_user = str(android_user or DEFAULT_ANDROID_USER)
        profile_path = self._calibration_path(serial, android_user)
        profile: dict[str, Any] = {}
        if profile_path.is_file():
            try:
                loaded = json.loads(profile_path.read_text(encoding="utf-8"))
                profile = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):
                profile = {}
        if (
            profile.get("schema") == 1
            and profile.get("model") == device.get("model")
            and profile.get("xiaohongshu_version") == device.get("xiaohongshu_version")
            and str(profile.get("android_user") or DEFAULT_ANDROID_USER) == android_user
        ):
            return profile
        calibrated = self._json("calibrate", "--serial", serial, "--android-user", android_user)
        if not isinstance(calibrated, dict) or calibrated.get("status") != "calibrated":
            raise RuntimeError("新设备首次发布预检失败")
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            json.dumps(calibrated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return calibrated

    def _media_dir(self) -> Path:
        return Path(get_settings().storage_dir) / "media"

    def stage_images(self, *, user_id: int, job_id: int, assets: list[Any]) -> list[Path]:
        images: list[Path] = []
        for asset in assets:
            if asset.asset_type != "image":
                raise RuntimeError("安卓手机发布当前仅支持图文素材")
            source = str(asset.file_path or "")
            candidate = Path(source)
            if not candidate.is_file():
                candidate = self._media_dir() / Path(source).name
            if not candidate.is_file() and source.startswith(("http://", "https://")):
                downloaded = download_asset_to_local(source, user_id, "image")
                if downloaded:
                    candidate = self._media_dir() / downloaded
            if not candidate.is_file():
                raise RuntimeError(f"无法读取图片素材：{source[:80]}")
            images.append(candidate.resolve())
        if not images:
            raise RuntimeError("至少需要一张图片")
        return images[:9]

    def prepare(self, *, serial: str, android_user: str = DEFAULT_ANDROID_USER, user_id: int, job_id: int, assets: list[Any]) -> dict[str, Any]:
        self.doctor(serial, android_user)
        images = self.stage_images(user_id=user_id, job_id=job_id, assets=assets)
        remote_paths = self._run("push", "--serial", serial, *[str(path) for path in images]).splitlines()
        self._run("open", "--serial", serial, "--android-user", str(android_user or DEFAULT_ANDROID_USER))
        return {"image_count": len(images), "remote_paths": remote_paths, "android_user": str(android_user or DEFAULT_ANDROID_USER)}

    def fill_for_review(
        self,
        *,
        serial: str,
        android_user: str = DEFAULT_ANDROID_USER,
        user_id: int,
        job_id: int,
        title: str,
        body: str,
        topics: list[str] | None = None,
    ) -> str:
        stage_dir = Path(get_settings().storage_dir) / "android" / f"u{user_id}" / f"job-{job_id}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        title_file = stage_dir / "title.txt"
        body_file = stage_dir / "body.txt"
        topics_file = stage_dir / "topics.json"
        title_file.write_text(title.strip(), encoding="utf-8")
        body_file.write_text(body.strip(), encoding="utf-8")
        topics_file.write_text(json.dumps(topics or [], ensure_ascii=False), encoding="utf-8")
        self._run(
            "fill",
            "--serial", serial,
            "--android-user", str(android_user or DEFAULT_ANDROID_USER),
            "--title-file", str(title_file),
            "--body-file", str(body_file),
            "--topics-file", str(topics_file),
        )
        file_name = f"xhs-android-u{user_id}-job-{job_id}.png"
        output = self._media_dir() / file_name
        output.parent.mkdir(parents=True, exist_ok=True)
        self._run("screenshot", "--serial", serial, "--output", str(output))
        return file_name

    def publish_direct(
        self,
        *,
        serial: str,
        android_user: str = DEFAULT_ANDROID_USER,
        user_id: int,
        job_id: int,
        assets: list[Any],
        title: str,
        body: str,
        topics: list[str] | None = None,
        product_keyword: str = "",
        scheduled_at: str | None = None,
    ) -> dict[str, Any]:
        """Push the reviewed payload and complete the phone UI publication."""
        if len(title.strip()) > 20:
            raise RuntimeError("小红书标题不能超过20个字符，请修改并重新审核后发布")
        android_user = str(android_user or DEFAULT_ANDROID_USER)
        device = self.doctor(serial, android_user)
        images = self.stage_images(user_id=user_id, job_id=job_id, assets=assets)
        remote_paths = self._run(
            "push", "--serial", serial, "--clear", *[str(path) for path in images],
        ).splitlines()
        self.ensure_calibrated(serial, device, android_user)
        stage_dir = Path(get_settings().storage_dir) / "android" / f"u{user_id}" / f"job-{job_id}"
        stage_dir.mkdir(parents=True, exist_ok=True)
        title_file = stage_dir / "title.txt"
        body_file = stage_dir / "body.txt"
        topics_file = stage_dir / "topics.json"
        title_file.write_text(title.strip(), encoding="utf-8")
        body_file.write_text(body.strip(), encoding="utf-8")
        topics_file.write_text(json.dumps(topics or [], ensure_ascii=False), encoding="utf-8")
        publish_args = [
            "publish",
            "--serial", serial,
            "--title-file", str(title_file),
            "--body-file", str(body_file),
            "--topics-file", str(topics_file),
            "--image-count", str(len(images)),
            "--android-user", android_user,
        ]
        if product_keyword.strip():
            publish_args.extend(["--product-keyword", product_keyword.strip()])
        if scheduled_at and scheduled_at.strip():
            publish_args.extend(["--scheduled-at", scheduled_at.strip()])
        result = self._json(*publish_args, timeout=360)
        if not isinstance(result, dict) or result.get("status") not in {"published", "scheduled"}:
            raise RuntimeError("手机端未返回发布成功或定时提交状态")
        return {**result, "remote_paths": remote_paths}
