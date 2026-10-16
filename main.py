from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parent


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Spider_XHS product platform.")
    parser.add_argument("--host", default="127.0.0.1", help="Backend host.")
    parser.add_argument("--port", type=int, default=8000, help="Backend port.")
    parser.add_argument("--reload", action="store_true", help="Enable Uvicorn reload.")
    parser.add_argument("--with-frontend", action="store_true", help="Also start the frontend Vite dev server.")
    parser.add_argument("--frontend-port", type=int, default=5173, help="Frontend dev server port.")
    parser.add_argument(
        "--frontend-host",
        default="0.0.0.0",
        help="Frontend dev server bind host. Use 0.0.0.0 for LAN access.",
    )
    return parser.parse_args(argv)


def resolve_npm_executable() -> str:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise FileNotFoundError("npm was not found on PATH; install Node.js or start the frontend manually.")
    return npm


def build_frontend_command(
    port: int,
    npm_executable: Optional[str] = None,
    host: str = "127.0.0.1",
) -> list[str]:
    npm = npm_executable or resolve_npm_executable()
    return [npm, "run", "dev", "--", "--host", host, "--port", str(port)]


def start_frontend(port: int, host: str = "0.0.0.0") -> Optional[subprocess.Popen]:
    frontend_dir = ROOT / "frontend"
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        print("frontend/package.json not found; skipping frontend startup.")
        return None

    command = build_frontend_command(port, host=host)
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"Starting frontend at http://{display_host}:{port} (bind: {host})")
    return subprocess.Popen(command, cwd=str(frontend_dir))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    # Resolve host/port: CLI args take precedence, then YAML/env config defaults
    host = args.host
    port = args.port
    try:
        from backend.app.core.config import get_settings
        settings = get_settings()
        # Use config values only when CLI args are at their defaults
        if host == "127.0.0.1" and settings.server_host:
            host = settings.server_host
        if port == 8000 and settings.server_port:
            port = settings.server_port
    except Exception:
        pass

    frontend_process = start_frontend(args.frontend_port, args.frontend_host) if args.with_frontend else None

    print(f"Starting backend at http://{host}:{port}")
    try:
        import uvicorn

        uvicorn.run("backend.app.main:app", host=host, port=port, reload=args.reload)
    finally:
        if frontend_process and frontend_process.poll() is None:
            frontend_process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
