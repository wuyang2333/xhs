from __future__ import annotations

import sys
from types import SimpleNamespace

from backend.app.adapters.xhs.pc_login_adapter import XhsPcLoginAdapter


def test_create_qrcode_prepares_webprofile_before_returning(monkeypatch):
    calls: list[tuple] = []

    class FakeXhsLoginApi:
        def generate_init_cookies(self):
            calls.append(("generate_init_cookies",))
            return {"a1": "temp-a1"}

        def generate_qrcode(self, cookies):
            calls.append(("generate_qrcode", dict(cookies)))
            return True, "成功", {
                "cookies": cookies,
                "qr_id": "qr-123",
                "code": "code-123",
                "qr_url": "https://example.test/qr",
            }

        def check_qrcode_status(self, qr_id, code, cookies):
            calls.append(("check_qrcode_status", qr_id, code, dict(cookies)))
            cookies["poll_ready"] = "yes"
            return False, "请扫描二维码", cookies

        def ensure_webprofile(self, cookies):
            calls.append(("ensure_webprofile", dict(cookies)))
            cookies["gid"] = "gid-123"
            return "gid-123"

    monkeypatch.setitem(
        sys.modules,
        "apis.xhs_pc_login_apis",
        SimpleNamespace(XHSLoginApi=FakeXhsLoginApi),
    )

    result = XhsPcLoginAdapter().create_qrcode()

    assert result == {
        "cookies": {"a1": "temp-a1", "poll_ready": "yes", "gid": "gid-123"},
        "qr_id": "qr-123",
        "code": "code-123",
        "qr_url": "https://example.test/qr",
    }
    assert [call[0] for call in calls] == [
        "generate_init_cookies",
        "generate_qrcode",
        "check_qrcode_status",
        "ensure_webprofile",
    ]
