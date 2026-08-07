#!/usr/bin/env python3
"""Offline restore. Stop the backend service before running this command."""
import argparse
from backend.app.services.backup_service import list_backups, restore_backup

parser = argparse.ArgumentParser()
parser.add_argument("backup_id", nargs="?")
parser.add_argument("--list", action="store_true")
parser.add_argument("--confirm", action="store_true")
args = parser.parse_args()
if args.list:
    for item in list_backups(): print(item["id"], item.get("created_at"), item.get("reason"), "valid=" + str(item.get("valid")))
elif not args.backup_id or not args.confirm:
    raise SystemExit("恢复需要：scripts/restore_backup.py BACKUP_ID --confirm（运行前先停止后端）")
else:
    print(restore_backup(args.backup_id))
