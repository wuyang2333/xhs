#!/usr/bin/env python3
"""导出模型配置到 config/model_configs.json，供 git 同步到其他机器。

用法：./.venv-py312/bin/python scripts/export_model_configs.py
"""
import json
import sqlite3
from pathlib import Path

db_path = Path("data/spider_xhs.db")
out_path = Path("config/model_configs.json")

c = sqlite3.connect(str(db_path))
rows = []
for r in c.execute(
    "SELECT name, model_type, provider, model_name, base_url, encrypted_api_key, is_default "
    "FROM model_configs WHERE user_id = 1 ORDER BY id"
):
    rows.append({
        "name": r[0], "model_type": r[1], "provider": r[2],
        "model_name": r[3], "base_url": r[4],
        "encrypted_api_key": r[5], "is_default": bool(r[6]),
    })
c.close()

out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"导出 {len(rows)} 条 → {out_path}")
