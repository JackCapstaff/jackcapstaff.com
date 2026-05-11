#!/usr/bin/env python3
"""
Management utilities for rehearsal_schedule JSON files.

Features:
- Rename a field/key across schedule objects (`works`, `rehearsals`, etc.)
- Find & replace works by `Work` id (reassign/merge) or by `Title` substring
- Backups and dry-run support

Usage examples:
  python tools/manage_schedule.py rename-field --old Title --new Name --target works --dry-run
  python tools/manage_schedule.py replace-work-id --old-id 12 --new-id 42 --merge --backup
  python tools/manage_schedule.py replace-work-title --find "Symphony" --replace "Sym." --ignore-case

This is a safe, offline management tool — it edits JSON files under site/data/schedules/.
"""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEDULES_DIR = Path("site/data/schedules")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = path.with_suffix(path.suffix + f".bak.{stamp}")
    shutil.copy2(path, dest)
    return dest


def rename_key_in_dict(d: dict, old: str, new: str) -> bool:
    if old in d:
        d[new] = d.pop(old)
        return True
    return False


def rename_field_in_records(records: list, old: str, new: str) -> int:
    changed = 0
    for r in records:
        if isinstance(r, dict) and old in r:
            r[new] = r.pop(old)
            changed += 1
    return changed


def recursive_replace_work_id(obj: Any, old_id: int, new_id: int) -> int:
    """Recursively replace occurrences of key 'Work' equal to old_id with new_id. Returns count."""
    count = 0
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k == "Work" and v == old_id:
                obj[k] = new_id
                count += 1
            else:
                count += recursive_replace_work_id(v, old_id, new_id)
    elif isinstance(obj, list):
        for item in obj:
            count += recursive_replace_work_id(item, old_id, new_id)
    return count


def replace_title_in_works(works: list, find: str, repl: str, ignore_case: bool) -> int:
    count = 0
    for w in works:
        if not isinstance(w, dict):
            continue
        title = w.get("Title") or w.get("title")
        if not isinstance(title, str):
            continue
        if ignore_case:
            if find.lower() in title.lower():
                # perform case-preserving replacement using a simple approach
                w_keys = [k for k in w.keys()]
                # replace in Title key only
                w["Title"] = title.replace(find, repl)
                count += 1
        else:
            if find in title:
                w["Title"] = title.replace(find, repl)
                count += 1
    return count


def do_rename_field(old: str, new: str, target: str, dry_run: bool, backup_files: bool) -> None:
    files = sorted(SCHEDULES_DIR.glob("*.json"))
    if not files:
        print("No schedule files found in", SCHEDULES_DIR)
        return
    total_changed = 0
    for p in files:
        data = load_json(p)
        changed = 0
        if target in ("works", "all") and "works" in data:
            if "works_cols" in data:
                data["works_cols"] = [new if c == old else c for c in data["works_cols"]]
            changed += rename_field_in_records(data.get("works", []), old, new)
        if target in ("rehearsals", "all") and "rehearsals" in data:
            if "rehearsals_cols" in data:
                data["rehearsals_cols"] = [new if c == old else c for c in data["rehearsals_cols"]]
            changed += rename_field_in_records(data.get("rehearsals", []), old, new)
        # Also try renaming keys in other record groups generically
        for key in ("allocation", "schedule", "timed"):
            if key in data:
                changed += rename_field_in_records(data.get(key, []), old, new)
        if changed:
            print(f"{p.name}: renamed {changed} occurrences of '{old}' → '{new}'")
            total_changed += changed
            if not dry_run:
                if backup_files:
                    bak = backup(p)
                    print("  backup:", bak.name)
                write_json(p, data)
    print(f"Done. Total changed: {total_changed}")


def do_replace_work_id(old_id: int, new_id: int, merge: bool, dry_run: bool, backup_files: bool, force: bool) -> None:
    files = sorted(SCHEDULES_DIR.glob("*.json"))
    if not files:
        print("No schedule files found in", SCHEDULES_DIR)
        return
    total_replacements = 0
    for p in files:
        data = load_json(p)
        # check works array for collisions
        works = data.get("works", [])
        has_old = any(isinstance(w, dict) and w.get("Work") == old_id for w in works)
        has_new = any(isinstance(w, dict) and w.get("Work") == new_id for w in works)
        if not has_old and not any(rec for rec in data.get("allocation", []) if rec.get("Work") == old_id):
            # nothing to do
            continue
        if has_new and not merge and not force:
            print(f"{p.name}: new id {new_id} already present; use --merge or --force to proceed")
            continue
        replacements = recursive_replace_work_id(data, old_id, new_id)
        # If renaming the work record itself and not merging, update the 'Work' key
        if has_old and not has_new:
            for w in works:
                if isinstance(w, dict) and w.get("Work") == old_id:
                    if not dry_run:
                        w["Work"] = new_id
                    replacements += 1
        # If merging (new exists), remove the old work record
        if has_old and merge and not dry_run:
            data["works"] = [w for w in works if not (isinstance(w, dict) and w.get("Work") == old_id)]
        if replacements:
            print(f"{p.name}: replaced {replacements} references of Work {old_id} → {new_id}")
            total_replacements += replacements
            if not dry_run:
                if backup_files:
                    bak = backup(p)
                    print("  backup:", bak.name)
                write_json(p, data)
    print(f"Done. Total replacements: {total_replacements}")


def do_replace_work_title(find: str, repl: str, ignore_case: bool, dry_run: bool, backup_files: bool) -> None:
    files = sorted(SCHEDULES_DIR.glob("*.json"))
    if not files:
        print("No schedule files found in", SCHEDULES_DIR)
        return
    total_changed = 0
    for p in files:
        data = load_json(p)
        works = data.get("works", [])
        changed = replace_title_in_works(works, find, repl, ignore_case)
        if changed:
            print(f"{p.name}: updated {changed} work titles")
            total_changed += changed
            if not dry_run:
                if backup_files:
                    bak = backup(p)
                    print("  backup:", bak.name)
                write_json(p, data)
    print(f"Done. Total title changes: {total_changed}")


def main():
    parser = argparse.ArgumentParser(description="Manage rehearsal_schedule JSON data (rename/replace)")
    sub = parser.add_subparsers(dest="cmd")

    p1 = sub.add_parser("rename-field", help="Rename a field/key across schedules")
    p1.add_argument("--old", required=True)
    p1.add_argument("--new", required=True)
    p1.add_argument("--target", choices=["works", "rehearsals", "all"], default="all")
    p1.add_argument("--dry-run", action="store_true")
    p1.add_argument("--backup", action="store_true", help="create timestamped .bak copies before writing")

    p2 = sub.add_parser("replace-work-id", help="Replace all references to a work id with another id")
    p2.add_argument("--old-id", type=int, required=True)
    p2.add_argument("--new-id", type=int, required=True)
    p2.add_argument("--merge", action="store_true", help="If new id exists, merge by removing old work record")
    p2.add_argument("--force", action="store_true", help="Force replacement even if new id exists")
    p2.add_argument("--dry-run", action="store_true")
    p2.add_argument("--backup", action="store_true")

    p3 = sub.add_parser("replace-work-title", help="Find & replace in work titles")
    p3.add_argument("--find", required=True)
    p3.add_argument("--replace", required=True)
    p3.add_argument("--ignore-case", action="store_true")
    p3.add_argument("--dry-run", action="store_true")
    p3.add_argument("--backup", action="store_true")

    args = parser.parse_args()
    if args.cmd == "rename-field":
        do_rename_field(args.old, args.new, args.target, args.dry_run, args.backup)
    elif args.cmd == "replace-work-id":
        do_replace_work_id(args.old_id, args.new_id, args.merge, args.dry_run, args.backup, args.force)
    elif args.cmd == "replace-work-title":
        do_replace_work_title(args.find, args.replace, args.ignore_case, args.dry_run, args.backup)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
