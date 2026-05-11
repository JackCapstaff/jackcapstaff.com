#!/usr/bin/env python3
"""One-time migration script to rename 'Unnamed: 2' to 'Day' in all schedules"""

from app import migrate_unnamed_columns_to_day

if __name__ == "__main__":
    count = migrate_unnamed_columns_to_day()
    print(f"✓ Successfully migrated {count} schedule(s)")
    print("  - Renamed 'Unnamed: 2' to 'Day' in rehearsals_cols")
    print("  - Renamed 'Unnamed: 2' to 'Day' in all rehearsal objects")
