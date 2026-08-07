"""测试全局前置。

必须在任何 backend 模块被导入之前执行——`tests/backend/test_api.py` 在模块级就
构造了 `TestClient(app)`，届时 `get_settings()` 已被缓存。pytest 保证 conftest.py
先于测试模块导入，所以这里是唯一可靠的位置。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# 把媒体/导出目录指向临时目录。此前 storage_dir 写死为 backend/app/storage，
# 两个走 generate-cover 的测试会把真实图片文件写进项目媒体目录——每跑一次
# 全量测试泄漏 2 个文件，长期污染用户的真实素材目录。
_STORAGE_TMPDIR = tempfile.mkdtemp(prefix="xhs-test-storage-")
os.environ.setdefault("STORAGE_DIR", _STORAGE_TMPDIR)
Path(_STORAGE_TMPDIR, "media").mkdir(parents=True, exist_ok=True)
Path(_STORAGE_TMPDIR, "exports").mkdir(parents=True, exist_ok=True)
