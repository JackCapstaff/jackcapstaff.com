# 2-Orchestration_organisation.py
import argparse
import re
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple

# =========================
# Config
# =========================
INPUT_FILE = "User_Input_1.xlsx"
SHEET_REHEARSALS = "Rehearsals"
SHEET_WORKS = "Works"

ALLOCATION_FILE = "All Rehearsal Times.xlsx"   # from script 1
ALLOCATION_SHEET = 0                           # default sheet

OUTPUT_FILE = "Rehearsal_schedule.xlsx"        # consumed by script 3

# Canonical desired column names (we'll resolve variants automatically)
WIND_COLS_CANON   = ["Flute", "Oboe", "Clarinet", "Bassoon", "Piccolo", "Cor Anglais", "Saxophone"]
BRASS_COLS_CANON  = ["Horn", "Trumpet", "Trombone", "Tuba"]
STRING_COLS_CANON = ["Violin 1", "Violin 2", "Viola", "Cello", "Bass", "Double Bass"]
PERC_COLS_CANON   = ["Percussion", "Timpani"]
PIANO_COLS_CANON  = ["Piano", "Celeste", "Celesta", "Keyboard"]
HARP_COLS_CANON   = ["Harp"]

SECTION_FLAGS = ["Percs", "Piano", "Harp", "Winds", "Brass", "Strings"]

# Group/parent-work column aliases (if present on the Works sheet, we use them)
GROUP_ALIASES = ["Group", "Work", "Parent Work", "Parent", "Collection", "Cycle", "Main Work", "Work Title"]

# Known aliases → canonical (for better matching)
ALIASES = {
    "coranglais": "Cor Anglais",
    "cor anglais": "Cor Anglais",
    "doublebass": "Double Bass",
    "contrabass": "Double Bass",
    "bass(double)": "Double Bass",
    "celeste": "Celeste",
    "celesta": "Celesta",
}

# Section weights for "Player Load" estimation (relative, not literal headcount)
WEIGHTS = {
    "WIND":   1.0,   # per instrument (Flute/Oboe/… when numeric given); else +1 for presence
    "BRASS":  1.5,
    "PERC":   2.0,   # Percussion players tend to add setup/time cost
    "PIANO":  1.0,
    "HARP":   1.2,
    "STRING": 0.6,   # per desk if numbers provided; else +4 baseline for presence
}
STRINGS_BASELINE_IF_PRESENT = 4.0

# =========================
# Column resolution helpers
# =========================
def norm(s: str) -> str:
    return str(s).strip().lower().replace(" ", "").replace("_", "")

def resolve_columns(df: pd.DataFrame, desired: List[str]) -> List[str]:
    """Return df columns best matching the desired list, with simple alias mapping."""
    if df is None or df.empty:
        return []
    colmap = {norm(c): c for c in df.columns}
    resolved = []
    for want in desired:
        key = norm(want)
        if key in ALIASES:
            key = norm(ALIASES[key])
        if key in colmap:
            resolved.append(colmap[key])
    return resolved

def gather_resolved_groups(works_df: pd.DataFrame) -> Dict[str, List[str]]:
    return {
        "WIND":   resolve_columns(works_df, WIND_COLS_CANON),
        "BRASS":  resolve_columns(works_df, BRASS_COLS_CANON),
        "STRING": resolve_columns(works_df, STRING_COLS_CANON),
        "PERC":   resolve_columns(works_df, PERC_COLS_CANON),
        "PIANO":  resolve_columns(works_df, PIANO_COLS_CANON),
        "HARP":   resolve_columns(works_df, HARP_COLS_CANON),
    }

def _first_matching_col(df: pd.DataFrame, aliases: List[str]) -> str | None:
    look = {norm(c): c for c in df.columns}
    for a in aliases:
        if norm(a) in look:
            return look[norm(a)]
    return None

# =========================
# IO helpers
# =========================
def load_rehearsals() -> pd.DataFrame:
    df = pd.read_excel(INPUT_FILE, sheet_name=SHEET_REHEARSALS)
    if "Rehearsal" not in df.columns:
        raise ValueError("Rehearsals sheet must contain a 'Rehearsal' column")
    df["Rehearsal"] = pd.to_numeric(df["Rehearsal"], errors="coerce").astype("Int64")
    return df

def load_works() -> pd.DataFrame:
    return pd.read_excel(INPUT_FILE, sheet_name=SHEET_WORKS)

def load_allocation() -> pd.DataFrame:
    return pd.read_excel(ALLOCATION_FILE, sheet_name=ALLOCATION_SHEET)

def normalise_flag(v) -> bool:
    if pd.isna(v): return False
    s = str(v).strip().upper()
    return s in {"Y", "YES", "TRUE", "T", "1"}

def extract_rehearsal_flags(rehearsals_df: pd.DataFrame) -> pd.DataFrame:
    flags = rehearsals_df[["Rehearsal"] + [f for f in SECTION_FLAGS if f in rehearsals_df.columns]].copy()
    for f in SECTION_FLAGS:
        if f not in flags.columns:
            flags[f] = False
        flags[f] = flags[f].apply(normalise_flag)
    return flags.set_index("Rehearsal")[SECTION_FLAGS]

# =========================
# Orchestration inspection / signatures / player-load
# =========================
def any_positive(row: pd.Series, cols: List[str]) -> bool:
    if not cols: return False
    for c in cols:
        if c in row.index:
            try:
                if pd.to_numeric(row[c], errors="coerce") > 0:
                    return True
            except Exception:
                pass
    return False

def _count_cell(x) -> float:
    """Count-style coercion: numeric>0 → numeric; text/non-empty → 1; else 0."""
    v = pd.to_numeric(x, errors="coerce")
    if pd.notna(v):
        return float(v) if v > 0 else 0.0
    s = str(x).strip().lower()
    if s and s not in {"", "0", "-", "n", "no", "none", "n/a"}:
        return 1.0
    return 0.0

def estimate_player_load(row: pd.Series, groups: Dict[str, List[str]]) -> float:
    load = 0.0
    # Winds / Brass / Percussion / Piano / Harp: sum counts × weight
    for col in groups["WIND"]:   load += WEIGHTS["WIND"]   * _count_cell(row.get(col))
    for col in groups["BRASS"]:  load += WEIGHTS["BRASS"]  * _count_cell(row.get(col))
    perc_count = 0.0
    for col in groups["PERC"]:   perc_count += _count_cell(row.get(col))
    load += WEIGHTS["PERC"] * perc_count
    # Keys / Harp
    for col in groups["PIANO"]:  load += WEIGHTS["PIANO"]  * _count_cell(row.get(col))
    for col in groups["HARP"]:   load += WEIGHTS["HARP"]   * _count_cell(row.get(col))
    # Strings: desk counts if available, otherwise baseline if any string present
    string_cols = groups["STRING"]
    if string_cols:
        string_sum = sum(_count_cell(row.get(c)) for c in string_cols)
        if string_sum > 0:
            load += WEIGHTS["STRING"] * string_sum
        elif any_positive(row, string_cols):
            load += STRINGS_BASELINE_IF_PRESENT
    return float(load)

def required_sections_for_work(row: pd.Series, groups: Dict[str, List[str]]) -> Dict[str, bool]:
    return {
        "Percs":   any_positive(row, groups["PERC"]),
        "Piano":   any_positive(row, groups["PIANO"]),
        "Harp":    any_positive(row, groups["HARP"]),
        "Winds":   any_positive(row, groups["WIND"]),
        "Brass":   any_positive(row, groups["BRASS"]),
        "Strings": any_positive(row, groups["STRING"]),
    }

def perc_profile(row: pd.Series, groups: Dict[str, List[str]]) -> int:
    c = 0
    for col in groups["PERC"]:
        c += int(_count_cell(row.get(col)))
    if c == 0:  return 0
    if c <= 2:  return 1
    return 2

# =========================
# Grouping / movement parsing
# =========================
_ROMAN = {"I":1,"II":2,"III":3,"IV":4,"V":5,"VI":6,"VII":7,"VIII":8,"IX":9,"X":10,
          "XI":11,"XII":12,"XIII":13,"XIV":14,"XV":15}

def parse_group_and_movement(title: str, group_hint: str | None) -> Tuple[str, str | None, int | None]:
    """
    Return (group_title, movement_label, movement_order).
    - Prefer group_hint if provided.
    - Else split Title on ':', ' – ', ' - ' (first occurrence) to detect a parent work.
    - Detect movement order from Roman numerals or leading integer.
    """
    s = str(title).strip()
    if group_hint:
        group = str(group_hint).strip()
        tail = s[len(group):].strip() if s.lower().startswith(group.lower()) else None
    else:
        # split only on the first separator
        m = re.split(r"\s*[:\-–]\s*", s, maxsplit=1)
        group = m[0].strip()
        tail = m[1].strip() if len(m) > 1 else None

    mov_label, mov_ord = None, None
    if tail:
        # Try Roman at start, e.g. "I. Allegro", "IV – Adagio"
        mr = re.match(r"^(I{1,3}|IV|V|VI{0,3}|IX|X)\b[.\-–:]?\s*(.*)$", tail, flags=re.IGNORECASE)
        if mr:
            mov_label = tail
            mov_ord = _ROMAN.get(mr.group(1).upper(), None)
        else:
            # Try leading integer "1.", "2 –", "3 Adagio"
            mn = re.match(r"^(\d{1,2})\b[.\-–:]?\s*(.*)$", tail)
            if mn:
                mov_label = tail
                mov_ord = int(mn.group(1))
            else:
                mov_label = tail
    return group or s, mov_label, mov_ord

# =========================
# Core
# =========================
def main():
    parser = argparse.ArgumentParser(description="Build per-rehearsal schedule table with grouping and player-load for script 3.")
    args = parser.parse_args()

    rehearsals_df = load_rehearsals()
    works_df = load_works()
    alloc_df = pd.read_excel(ALLOCATION_FILE, sheet_name=ALLOCATION_SHEET)
    alloc_df = alloc_df[~alloc_df["Title"].astype(str).str.startswith("[Summary]")]

    # Resolve orchestration columns that actually exist
    groups = gather_resolved_groups(works_df)

    # Identify rehearsal columns in the allocation file (e.g., 'Rehearsal 1', 'Rehearsal 2', ...)
    reh_cols = [c for c in alloc_df.columns if str(c).strip().startswith("Rehearsal ")]
    col_to_num: Dict[str, int] = {}
    for c in reh_cols:
        try:
            col_to_num[c] = int(str(c).split()[-1])
        except Exception:
            pass
    if not col_to_num:
        raise ValueError("No 'Rehearsal N' columns found in the allocation file.")
    if "Title" not in alloc_df.columns:
        raise ValueError("Allocation file must contain a 'Title' column.")

    # Determine group hint column (if any) from the Works sheet
    group_col = _first_matching_col(works_df, GROUP_ALIASES)

    # Precompute signatures and player-load per Title from Works
    works_indexed = works_df.set_index("Title", drop=False)
    sig_rows = {}
    for t, wr in works_indexed.groupby(level=0):
        r = wr.iloc[0]
        sig_rows[t] = {
            **required_sections_for_work(r, groups),
            "PercProfile": perc_profile(r, groups),
            "Player Load": estimate_player_load(r, groups),
        }
        # Also store a group hint if present on Works
        if group_col and group_col in r.index and pd.notna(r[group_col]):
            sig_rows[t]["GroupHint"] = str(r[group_col]).strip()
        else:
            sig_rows[t]["GroupHint"] = None

    # Rehearsal flags (for diagnostics only here)
    reh_flags = extract_rehearsal_flags(rehearsals_df)

    # Build rows
    rows = []
    warnings = []
    for _, row in alloc_df.iterrows():
        title = str(row["Title"])
        sig = sig_rows.get(title, None)
        group_hint = sig["GroupHint"] if sig else None

        for col, rnum in col_to_num.items():
            mins = pd.to_numeric(row.get(col, 0), errors="coerce")
            if pd.isna(mins) or mins <= 0:
                continue

            # Compute Group / Movement from title (with hint)
            group_title, mov_label, mov_ord = parse_group_and_movement(title, group_hint)

            # Flags availability diagnostic
            flags = reh_flags.loc[rnum] if rnum in reh_flags.index else pd.Series({f: False for f in SECTION_FLAGS})
            allowed = True
            if sig:
                for f in SECTION_FLAGS:
                    if sig.get(f, False) and not bool(flags.get(f, False)) and f in ["Percs", "Piano", "Harp", "Brass"]:
                        allowed = False
                        break
            if not allowed:
                warnings.append(f"[Mismatch] '{title}' has {int(mins)} min in Rehearsal {rnum} but required sections not available that night.")

            rows.append({
                "Rehearsal": int(rnum),
                "Group": group_title,
                "Title": title,
                "Movement Label": mov_label,
                "Movement #": mov_ord,
                "Rehearsal Time (minutes)": int(round(float(mins))),
                # carry signatures and player load for Script 3 (saves recomputing)
                "Percs": int(sig.get("Percs", 0)) if sig else 0,
                "Piano": int(sig.get("Piano", 0)) if sig else 0,
                "Harp": int(sig.get("Harp", 0)) if sig else 0,
                "Winds": int(sig.get("Winds", 0)) if sig else 0,
                "Brass": int(sig.get("Brass", 0)) if sig else 0,
                "Strings": int(sig.get("Strings", 0)) if sig else 0,
                "PercProfile": int(sig.get("PercProfile", 0)) if sig else 0,
                "Player Load": float(sig.get("Player Load", 0.0)) if sig else 0.0,
            })

    if not rows:
        out_df = pd.DataFrame(columns=["Rehearsal", "Group", "Title", "Rehearsal Time (minutes)"])
        out_df.to_excel(OUTPUT_FILE, index=False)
        print(f"WARNING: no rows produced. Wrote empty {OUTPUT_FILE}.")
        return

    out_df = pd.DataFrame(rows)
    out_df = out_df.sort_values(["Rehearsal", "Group", "Movement #", "Title"]).reset_index(drop=True)
    out_df.to_excel(OUTPUT_FILE, index=False)
    print(f"Saved: {OUTPUT_FILE} ({len(out_df)} rows)")

    # Diagnostics
    print("\n=== Orchestration columns resolved ===")
    grps = gather_resolved_groups(works_df)
    for k, v in grps.items():
        print(f"{k:7s}: {', '.join(v) if v else '(none found)'}")

    if warnings:
        with open("orchestration_warnings.txt", "w", encoding="utf-8") as f:
            for w in warnings:
                f.write(w + "\n")
        print(f"Wrote {len(warnings)} orchestration warning(s) to orchestration_warnings.txt")

if __name__ == "__main__":
    main()
