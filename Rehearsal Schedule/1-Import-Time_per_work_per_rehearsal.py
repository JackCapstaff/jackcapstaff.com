# allocate_rehearsals.py
import argparse
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

# =========================
# Config
# =========================
INPUT_FILE = "User_Input_1.xlsx"
SHEET_WORKS = "Works"
SHEET_REHEARSALS = "Rehearsals"

OUTPUT_FILE = "All Rehearsal Times.xlsx"
WARNINGS_FILE = "allocation_warnings.txt"

# Granularity (minutes) – all allocations are multiples of G
DEFAULT_GRANULARITY = 5

# Specialist sections to consider (Y flags on rehearsals)
SPECIAL_SECTIONS = ["Percs", "Piano", "Harp", "Brass", "Soloist"]


# Orchestration columns (numeric > 0 counts as present)
WIND_COLS   = ["Flute", "Oboe", "Clarinet", "Bassoon", "Piccolo", "Cor Anglais", "Saxophone"]
BRASS_COLS  = ["Horn", "Trumpet", "Trombone", "Tuba"]
STRING_COLS = ["Violin 1", "Violin 2", "Viola", "Cello", "Bass", "Double Bass"]
PERC_COLS   = ["Percussion", "Timpani"]
PIANO_COLS  = ["Piano", "Celeste", "Celesta", "Keyboard"]
HARP_COLS   = ["Harp"]
SOLOIST_COLS = ["Soloist", "Solo Voice", "Solo Instrument"]

# Behavior tunables
MULT_Y = 3.0          # multiplier for a mid rehearsal that matches a needed specialist section
ALPHA_MIN = 0.20      # min per appearance = ceil(max(G, ALPHA_MIN * duration) / G) * G
BETA_MAX = 3.0        # nominal max per appearance = ceil(BETA_MAX * duration / G) * G
SPREAD_ALPHA = 1.0    # strength of spread penalty vs. ideal even spacing
RECENCY_BONUS = 0.6   # small bonus for being far from last placement
STACKING_LAMBDA = 0.5 # penalty for stacking minutes of the same work on the same mid rehearsal
FLEX_UP_PCT = 0.10    # final mid fill: up to +10% per work beyond its required minutes
SOLOIST_MULT_Y = 6.0  # much stronger than other sections so soloist nights pull time
SOLOIST_MAX_MULT = 6.0

# =========================
# Parsing helpers
# =========================
TRUTHY = {"Y", "YES", "TRUE", "T", "1"}



def normalise_flag(v) -> bool:
    if pd.isna(v):
        return False
    return str(v).strip().upper() in TRUTHY

import re

import re
from datetime import time, datetime

def _to_minutes(value) -> float:
    """Robust HH:MM[:SS]/HH.MM/HHMM/Excel-time/datetime -> minutes since 00:00."""
    if pd.isna(value):
        return np.nan

    # Already a datetime or time?
    if isinstance(value, time):
        return float(value.hour * 60 + value.minute)
    if isinstance(value, (datetime, np.datetime64, pd.Timestamp)):
        try:
            ts = pd.to_datetime(value, errors="coerce")
            if pd.notna(ts):
                return float(ts.hour * 60 + ts.minute)
        except Exception:
            pass

    s = str(value).strip()
    # normalise weird unicode colon
    s = s.replace("：", ":")

    # 1) HH:MM[:SS]  (accept optional seconds)
    m = re.fullmatch(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2))
        if 0 <= hh < 24 and 0 <= mm < 60:
            return float(hh * 60 + mm)

    # 2) HH.MM  (13.30 -> 13:30)
    m = re.fullmatch(r"^(\d{1,2})\.(\d{1,2})$", s)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2))
        if 0 <= hh < 24 and 0 <= mm < 60:
            return float(hh * 60 + mm)

    # 3) HHMM  (1330 -> 13:30)
    m = re.fullmatch(r"^(\d{3,4})$", s)
    if m:
        val = int(m.group(1)); hh = val // 100; mm = val % 100
        if 0 <= hh < 24 and 0 <= mm < 60:
            return float(hh * 60 + mm)

    # 4) Excel fraction-of-day or raw minutes
    try:
        f = float(s)
        if 0 <= f < 1:          # 0.5 -> 12:00
            return float(round(f * 24 * 60))
        if 1 <= f < 24 * 60:    # 90 -> 01:30
            return float(f)
    except Exception:
        pass

    # 5) Last chance: pandas parser on strings
    try:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.notna(ts):
            return float(ts.hour * 60 + ts.minute)
    except Exception:
        pass

    return np.nan


def _parse_break_cell(x) -> float:
    """Parse mixed Break entries: 15, '15', '00:20', Excel times."""
    m = pd.to_numeric(x, errors="coerce")
    if pd.notna(m):
        return float(m)
    t = pd.to_datetime(x, errors="coerce")
    if pd.notna(t):
        return float(t.hour * 60 + t.minute)
    s = str(x).strip()
    if ":" in s:
        p = s.split(":")
        if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
            return float(int(p[0]) * 60 + int(p[1]))
    return np.nan

def any_positive(row: pd.Series, cols: List[str]) -> bool:
    for c in cols:
        if c not in row.index:
            continue
        v = row[c]
        if pd.isna(v):
            continue
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"", "-", "n", "no", "none", "n/a"}:
                continue
            try:
                if float(s) > 0:
                    return True
            except Exception:
                continue
        else:
            try:
                if float(v) > 0:
                    return True
            except Exception:
                continue
    return False

def required_sections_for_work(row: pd.Series) -> Dict[str, bool]:
    return {
        "Percs": any_positive(row, PERC_COLS),
        "Piano": any_positive(row, PIANO_COLS),
        "Harp":  any_positive(row, HARP_COLS),
        "Brass": any_positive(row, BRASS_COLS),
        "Soloist": any_positive(row, SOLOIST_COLS),
    }

# =========================
# Works normalization
# =========================
def _first_matching_col(df: pd.DataFrame, aliases: List[str]) -> str | None:
    lookup = {c.strip().lower(): c for c in df.columns}
    for a in aliases:
        k = a.strip().lower()
        if k in lookup:
            return lookup[k]
    return None

def normalise_works_columns(works_df: pd.DataFrame) -> pd.DataFrame:
    """
    Create canonical numeric columns:
      - duration_norm (minutes)
      - difficulty_norm (>=0.1)
    using flexible header matching and safe numeric coercion.
    """
    duration_aliases   = ["duration", "duration (mins)", "duration mins", "length", "minutes", "time", "min"]
    difficulty_aliases = ["difficulty", "diff", "weight", "priority", "complexity"]

    dur_col  = _first_matching_col(works_df, duration_aliases)
    diff_col = _first_matching_col(works_df, difficulty_aliases)

    # Duration → minutes
    if dur_col is None:
        dur = pd.Series(0.0, index=works_df.index, dtype=float)
    else:
        # Try numeric first
        dur_numeric = pd.to_numeric(works_df[dur_col], errors="coerce")
        # Try hh:mm per cell where numeric failed
        def _parse_hhmm(x):
            if pd.isna(x):
                return np.nan
            s = str(x).strip()
            if ":" in s:
                p = s.split(":")
                if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
                    return int(p[0]) * 60 + int(p[1])
            # Excel time fallback
            t = pd.to_datetime(x, errors="coerce")
            if pd.notna(t):
                return int(t.hour) * 60 + int(t.minute)
            return np.nan
        hhmm = works_df[dur_col].apply(_parse_hhmm)
        dur = dur_numeric.fillna(hhmm).fillna(0.0).astype(float)

    # Difficulty
    if diff_col is None:
        diff = pd.Series(1.0, index=works_df.index, dtype=float)
    else:
        diff = pd.to_numeric(works_df[diff_col], errors="coerce").fillna(1.0).astype(float)
        diff = diff.clip(lower=0.1)

    out = works_df.copy()
    out["duration_norm"]   = dur
    out["difficulty_norm"] = diff
    return out

# =========================
# IO
# =========================
def load_works() -> pd.DataFrame:
    df = pd.read_excel(INPUT_FILE, sheet_name=SHEET_WORKS)
    if "Title" not in df.columns:
        raise ValueError("Works sheet must contain a 'Title' column.")
    return normalise_works_columns(df)

def load_rehearsals() -> pd.DataFrame:
    df = pd.read_excel(INPUT_FILE, sheet_name=SHEET_REHEARSALS)
    if "Rehearsal" not in df.columns:
        raise ValueError("Rehearsals sheet must contain a 'Rehearsal' column")

    # Normalize identifiers
    df["Rehearsal"] = pd.to_numeric(df["Rehearsal"], errors="coerce").astype("Int64")
    df["Date"] = pd.to_datetime(df.get("Date"), errors="coerce").dt.date

    # --- Parse Start/End into minutes since midnight ---
    start_mins = df["Start Time"].apply(_to_minutes) if "Start Time" in df.columns else pd.Series(np.nan, index=df.index)
    end_mins   = df["End Time"].apply(_to_minutes)   if "End Time"   in df.columns else pd.Series(np.nan, index=df.index)

    # warn if a row will fall back
    bad_rows = df.index[start_mins.isna() | end_mins.isna()]
    for i in bad_rows:
        rid = df.loc[i, "Rehearsal"]
        sv = df.loc[i, "Start Time"] if "Start Time" in df.columns else None
        ev = df.loc[i, "End Time"] if "End Time" in df.columns else None
        print(f"[parse] Rehearsal {rid}: could not parse Start/End ('{sv}'/'{ev}'); using defaults 19:15–21:30")

    # defaults for the NaNs (19:15 and 21:30)
    start_mins = pd.to_numeric(start_mins, errors="coerce").fillna(19*60 + 15)
    end_mins   = pd.to_numeric(end_mins,   errors="coerce").fillna(21*60 + 30)

    # Break parsing unchanged, but keep the robust helper you already have
    br = df.get("Break")
    if br is not None:
        br = br.apply(_parse_break_cell).fillna(0.0)
    else:
        br = pd.Series(0.0, index=df.index)

    gross = end_mins - start_mins
    gross = gross.where(gross >= 0, gross + 24*60)
    df["Break (minutes)"] = br.astype(float)
    df["Duration"] = (pd.to_numeric(gross, errors="coerce").fillna(0.0) - br).clip(lower=0.0)

    # Start DateTime (nice to have for ordering/labels)
    start_hh = (start_mins // 60).astype(int).astype(str).str.zfill(2)
    start_mm = (start_mins % 60).astype(int).astype(str).str.zfill(2)
    start_hhmm = start_hh + ":" + start_mm
    date_str = np.where(pd.notna(df["Date"]), df["Date"].astype(str), "2000-01-01")
    df["Start DateTime"] = pd.to_datetime(date_str + " " + start_hhmm, errors="coerce")
    df["Start DateTime"] = df["Start DateTime"].fillna(pd.to_datetime("2000-01-01 19:15"))

    # ---- sanity debug (comment out once happy) ----
    print("\n[DEBUG] NET capacities per rehearsal (expect 120 for 19:15–21:30 with 15 break):")
    for i, r in df.iterrows():
        s = int(start_mins.iloc[i]); e = int(end_mins.iloc[i]); b = int(df.loc[i, "Break (minutes)"])
        sH, sM = divmod(s, 60); eH, eM = divmod(e, 60)
        print(f"  {int(r['Rehearsal'])}: {sH:02d}:{sM:02d}–{eH:02d}:{eM:02d}  break={b:02d}  → Duration={int(r['Duration'])}")

    return df


# =========================
# Tokenized proportional allocation utility
# =========================
def largest_remainder(weights: pd.Series, total_tokens: int) -> pd.Series:
    """Apportion integer tokens that sum to total_tokens using largest remainder method."""
    n = len(weights)
    if total_tokens <= 0 or n == 0:
        return pd.Series(0, index=weights.index, dtype=int)
    w = pd.to_numeric(weights, errors="coerce").fillna(0.0).astype(float)
    if w.sum() <= 0:
        raw = pd.Series(float(total_tokens) / n, index=w.index)
    else:
        raw = w / w.sum() * float(total_tokens)
    base = np.floor(raw).astype(int)
    remainder = (raw - base).sort_values(ascending=False)
    need = int(total_tokens - base.sum())
    if need > 0:
        base.loc[remainder.index[:need]] += 1
    return base.astype(int)

# =========================
# Global requirement (tokens then minutes)
# =========================
def compute_required_minutes(works_df: pd.DataFrame, snapped_total_minutes: int, G: int) -> pd.Series:
    """Return required minutes per work, realizable in G-minute chunks, summing to snapped_total_minutes."""
    tokens_total = int(snapped_total_minutes // G)
    w = pd.to_numeric(works_df["duration_norm"], errors="coerce").fillna(0.0) \
        * pd.to_numeric(works_df["difficulty_norm"], errors="coerce").fillna(1.0)
    tks = largest_remainder(w, tokens_total)
    return (tks * G).astype(int)

# =========================
# Bookend (first & last) allocations
# =========================
def bookend_allocations(
    works_df: pd.DataFrame,
    first_tokens: int,
    last_tokens: int,
    G: int,
) -> Tuple[pd.Series, pd.Series]:
    """
    Allocate tokens for first & last rehearsals.
    - Each is filled exactly to its token capacity.
    - If capacity allows, each rehearsal includes all works (≥1 token each).
    - Otherwise, ensure across first+last that each work gets ≥1 token when feasible.
    """
    idx = works_df.index
    n = len(idx)
    weights = pd.to_numeric(works_df["duration_norm"], errors="coerce").fillna(0.0) \
              * pd.to_numeric(works_df["difficulty_norm"], errors="coerce").fillna(1.0)

    base_first = pd.Series(0, index=idx, dtype=int)
    base_last  = pd.Series(0, index=idx, dtype=int)

    # Per-rehearsal baseline: if tokens >= n, give everyone 1 token on that rehearsal
    if first_tokens >= n:
        base_first[:] = 1
    if last_tokens >= n:
        base_last[:] = 1

    # If neither can cover everyone individually, try to ensure across both that each work gets ≥1 token
    if (first_tokens < n or last_tokens < n) and (first_tokens + last_tokens >= n):
        # Count how many baseline tokens we've already assigned across both
        covered = (base_first + base_last) >= 1
        f_rem = int(first_tokens - int(base_first.sum()))
        l_rem = int(last_tokens - int(base_last.sum()))
        # Distribute one baseline token per uncovered work across first/last
        # Prefer the rehearsal with more baseline capacity remaining
        order = list(idx)  # stable order; could also sort by weight if desired
        for i in order:
            if covered.loc[i]:
                continue
            if f_rem <= 0 and l_rem <= 0:
                break
            # Choose destination
            choose_first = f_rem >= l_rem and f_rem > 0
            if choose_first:
                base_first.loc[i] += 1; f_rem -= 1
            else:
                base_last.loc[i]  += 1; l_rem -= 1
            covered.loc[i] = True

    # Remainders by proportional split on each rehearsal separately
    f_rem = int(max(0, first_tokens - int(base_first.sum())))
    l_rem = int(max(0,  last_tokens - int(base_last.sum())))

    # On each rehearsal, allocate remaining tokens proportionally to weights
    add_first = largest_remainder(weights, f_rem) if f_rem > 0 else pd.Series(0, index=idx, dtype=int)
    add_last  = largest_remainder(weights, l_rem) if l_rem > 0 else pd.Series(0, index=idx, dtype=int)

    first_alloc_tokens = (base_first + add_first).astype(int)
    last_alloc_tokens  = (base_last  + add_last ).astype(int)

    # Final sanity
    assert int(first_alloc_tokens.sum()) == int(first_tokens)
    assert int(last_alloc_tokens.sum())  == int(last_tokens)

    return first_alloc_tokens * G, last_alloc_tokens * G

# =========================
# Middle rehearsals allocation
# =========================
@dataclass
class MidBounds:
    min_slot_tokens: int
    max_slot_tokens: int

def per_slot_bounds(duration_min: float, rem_minutes: int, n_mid: int, G: int, needs_soloist: bool) -> MidBounds:
    # Min per slot
    min_slot = int(np.ceil(max(G, ALPHA_MIN * duration_min) / G) * G)

    # Max per slot (bigger if soloist)
    cap_mult = SOLOIST_MAX_MULT if needs_soloist else BETA_MAX
    cap_nom = int(np.ceil(cap_mult * duration_min / G) * G)

    # Feasibility bump
    need_even = 0
    if n_mid > 0:
        need_even = int(np.ceil(max(0, rem_minutes) / max(1, n_mid) / G) * G)

    max_slot = max(min_slot, cap_nom, need_even)
    return MidBounds(min_slot // G, max_slot // G)

def weights_for_mid_rehearsals(row: pd.Series, flags_df: pd.DataFrame, mid_rehs: List[int]) -> Dict[int, float]:
    """Base score per mid rehearsal for a given work: capacity × specialist multiplier."""
    req = required_sections_for_work(row)
    w: Dict[int, float] = {}
    for r in mid_rehs:
        if r not in flags_df.index:
            w[r] = 0.0
            continue
        base = float(flags_df.loc[r, "_capacity"])  # minutes
        if base <= 0:
            w[r] = 0.0
            continue

        mult = 1.0
        # ordinary specialist sections
        for s in SPECIAL_SECTIONS:
            if s == "Soloist":
                continue  # handle Soloist below with stronger factor
            if req.get(s, False) and bool(flags_df.loc[r, s]):
                mult *= MULT_Y

        # extra Soloist pull (much stronger)
        if req.get("Soloist", False) and bool(flags_df.loc[r, "Soloist"]):
            mult *= SOLOIST_MULT_Y

        w[r] = base * mult
    return w


def allocate_mids(
    out_df: pd.DataFrame,
    works_df: pd.DataFrame,
    flags_df: pd.DataFrame,
    mid_rehs: List[int],
    rem_req_minutes: pd.Series,
    G: int,
    warnings: List[str]
) -> None:
    """
    Mutates out_df in-place, filling columns f"Rehearsal {r}" for r in mid_rehs.
    flags_df has index Rehearsal with boolean columns for sections and a '_capacity' column (snapped minutes).
    """
    # Convert capacities to tokens
    rem_cap_tokens: Dict[int, int] = {}
    for r in mid_rehs:
        cap_min = int(flags_df.loc[r, "_capacity"])
        used = int(out_df[f"Rehearsal {r}"].sum())  # should be 0 at entry
        rem_cap_tokens[r] = cap_min // G - used // G

    # Precompute bounds per work using normalized duration
    dur = pd.to_numeric(works_df["duration_norm"], errors="coerce").fillna(10.0)
    bounds: Dict[int, MidBounds] = {}
    for idx in out_df.index:
        rem_i = int(rem_req_minutes.loc[idx])
        needs_solo = required_sections_for_work(out_df.loc[idx]).get("Soloist", False)
        bounds[idx] = per_slot_bounds(float(dur.loc[idx]), rem_i, len(mid_rehs), G, needs_solo)


    # Helper to place up to 'tok' tokens on rehearsal r for work idx
    def place_tokens(idx: int, r: int, tok: int) -> int:
        if tok <= 0:
            return 0
        if rem_cap_tokens.get(r, 0) <= 0:
            return 0
        col = f"Rehearsal {r}"
        cur_tokens_here = int(out_df.loc[idx, col] // G)
        # respect per-slot max
        allowed = max(0, bounds[idx].max_slot_tokens - cur_tokens_here)
        can = min(tok, allowed, rem_cap_tokens[r])
        if can <= 0:
            return 0
        out_df.loc[idx, col] = int(out_df.loc[idx, col]) + can * G
        rem_cap_tokens[r] -= can
        return can

    # Coverage: ensure at least one placement on a Y mid for each needed section (if such mids exist)
    for s in SPECIAL_SECTIONS:
        y_mids = [r for r in mid_rehs if r in flags_df.index and bool(flags_df.loc[r, s])]
        if not y_mids:
            continue
        # works needing this section
        candidates = [idx for idx in out_df.index if required_sections_for_work(out_df.loc[idx]).get(s, False)
                      and int(rem_req_minutes.loc[idx]) > 0]
        if not candidates:
            continue
        # weight maps for ordering
        wts_map = {idx: weights_for_mid_rehearsals(out_df.loc[idx], flags_df, mid_rehs) for idx in out_df.index}
        for idx in candidates:
            # already on any Y mid?
            already = any(out_df.loc[idx, f"Rehearsal {r}"] > 0 for r in y_mids)
            if already:
                continue
            # try to put min-slot (or what's left) on best Y
            need_min_tok = min(bounds[idx].min_slot_tokens, int(np.ceil(rem_req_minutes.loc[idx] / G)))
            if need_min_tok <= 0:
                continue
            y_sorted = sorted(y_mids, key=lambda r: (rem_cap_tokens.get(r, 0), wts_map[idx].get(r, 0.0)), reverse=True)
            placed = place_tokens(idx, y_sorted[0], need_min_tok)
            if placed < need_min_tok:
                # Try small displacement: free one token at a time from donors not needing this section
                to_free = need_min_tok - placed
                r_y = y_sorted[0]
                donors = [j for j in out_df.index
                          if j != idx
                          and int(out_df.loc[j, f"Rehearsal {r_y}"] // G) >= 1
                          and not required_sections_for_work(out_df.loc[j]).get(s, False)]
                # Prefer donors with lowest score on r_y
                donors.sort(key=lambda j: float(wts_map.get(j, {}).get(r_y, 0.0)))
                freed = 0
                for j in donors:
                    if freed >= to_free:
                        break
                    # find alternate r2 for donor j
                    for r2 in mid_rehs:
                        if r2 == r_y:
                            continue
                        if rem_cap_tokens.get(r2, 0) <= 0:
                            continue
                        cur_on_r2 = int(out_df.loc[j, f"Rehearsal {r2}"] // G)
                        if cur_on_r2 >= bounds[j].max_slot_tokens:
                            continue
                        # move 1 token from r_y -> r2
                        out_df.loc[j, f"Rehearsal {r_y}"] -= G
                        out_df.loc[j, f"Rehearsal {r2}"] += G
                        rem_cap_tokens[r2] -= 1
                        rem_cap_tokens[r_y] += 1
                        freed += 1
                        break
                # now try to complete coverage
                placed += place_tokens(idx, r_y, to_free if rem_cap_tokens.get(r_y, 0) > 0 else 0)
            if placed == 0:
                warnings.append(f"[Coverage] Could not place '{out_df.loc[idx, 'Title']}' on a Y mid for {s}.")

    # Spread remaining across mids
    # Ordering: works that need more specialist types first, then by remaining need
    def needs_extras_count(idx: int) -> int:
        req = required_sections_for_work(out_df.loc[idx])
        return int(req["Percs"]) + int(req["Piano"]) + int(req["Harp"]) + int(req["Brass"]) + int(req["Soloist"])


    order = list(out_df.index)
    order.sort(key=lambda i: (needs_extras_count(i), int(rem_req_minutes.loc[i])), reverse=True)

    reh_pos = {r: i for i, r in enumerate(mid_rehs)}
    n_mid = len(mid_rehs)

    for idx in order:
        remaining_minutes = int(rem_req_minutes.loc[idx])
        # Subtract what coverage may have already placed
        already_mid = sum(int(out_df.loc[idx, f"Rehearsal {r}"]) for r in mid_rehs)
        need_min = max(0, remaining_minutes - already_mid)
        tokens_needed = int(np.ceil(need_min / G)) if need_min > 0 else 0
        if tokens_needed <= 0:
            continue

        row = out_df.loc[idx]
        wts = weights_for_mid_rehearsals(row, flags_df, mid_rehs)

        last_pos = None
        for k in range(tokens_needed):
            # Ideal evenly spaced target position in [0, n_mid-1]
            target_pos = (k + 0.5) * (n_mid / max(1, tokens_needed)) - 0.5
            best_r, best_score = None, -1e18
            for r in mid_rehs:
                if rem_cap_tokens.get(r, 0) <= 0:
                    continue
                col = f"Rehearsal {r}"
                cur_tok_here = int(out_df.loc[idx, col] // G)
                # respect per-slot bounds
                if cur_tok_here >= bounds[idx].max_slot_tokens:
                    continue
                pos = reh_pos[r]
                # Components
                base = wts.get(r, 0.0)
                spread_pen = SPREAD_ALPHA * abs(pos - target_pos)
                rec = (RECENCY_BONUS * abs(pos - last_pos)) if last_pos is not None else 0.0
                stack_pen = STACKING_LAMBDA * (cur_tok_here / max(1, bounds[idx].max_slot_tokens))
                score = base + rec - spread_pen - stack_pen
                if score > best_score:
                    best_r, best_score = r, score
            if best_r is None:
                break
            got = place_tokens(idx, best_r, 1)
            if got == 0:
                # try any other feasible r by descending base weight
                for r in sorted(mid_rehs, key=lambda rr: wts.get(rr, 0.0), reverse=True):
                    got = place_tokens(idx, r, 1)
                    if got > 0:
                        last_pos = reh_pos[r]
                        break
                if got == 0:
                    break
            else:
                last_pos = reh_pos[best_r]

    # Final mid fill (use leftover capacity) up to FLEX_UP_PCT extra per work
    total_left_tokens = sum(max(0, t) for t in rem_cap_tokens.values())
    if total_left_tokens > 0:
        # allowance in tokens per work
        req_minutes = out_df["Required Minutes"]
        extra_allow = {idx: int(round((float(req_minutes.loc[idx]) / G) * FLEX_UP_PCT)) for idx in out_df.index}
        # wts map per work for mids
        wts_map = {idx: weights_for_mid_rehearsals(out_df.loc[idx], flags_df, mid_rehs) for idx in out_df.index}
        while True:
            # pick rehearsal with most spare
            r_pick = None
            best_cap = 0
            for r in mid_rehs:
                if rem_cap_tokens.get(r, 0) > best_cap:
                    best_cap = rem_cap_tokens.get(r, 0); r_pick = r
            if r_pick is None or best_cap <= 0:
                break
            # pick best work for this rehearsal
            best_idx, best_score = None, -1e18
            for idx in out_df.index:
                if extra_allow[idx] <= 0:
                    continue
                col = f"Rehearsal {r_pick}"
                cur_tok_here = int(out_df.loc[idx, col] // G)
                if cur_tok_here >= bounds[idx].max_slot_tokens:
                    continue
                score = wts_map[idx].get(r_pick, 0.0)
                if score > best_score:
                    best_idx, best_score = idx, score
            if best_idx is None:
                break
            got = place_tokens(best_idx, r_pick, 1)
            if got == 0:
                break
            extra_allow[best_idx] -= 1

# =========================
# Allocate across all rehearsals
# =========================
def allocate_across_rehearsals(
    works_df: pd.DataFrame,
    rehearsals_df: pd.DataFrame,
    req_minutes: pd.Series,
    G: int
) -> Tuple[pd.DataFrame, List[str]]:
    # Sort rehearsals by their appearance order in the sheet (or Start DateTime if you prefer)
    df = rehearsals_df.sort_values(by="Rehearsal").copy()

    all_rehs = [int(r) for r in df["Rehearsal"] if pd.notna(r)]
    if len(all_rehs) < 2:
        raise ValueError("Need at least two rehearsals to do first/last allocation.")
    first_r, last_r = all_rehs[0], all_rehs[-1]
    mid_rehs = [r for r in all_rehs if r not in (first_r, last_r)]

    # Build flags (boolean) and capacities (snapped to G)
    flag_cols = [c for c in SPECIAL_SECTIONS if c in rehearsals_df.columns]
    flags_df = rehearsals_df[["Rehearsal", "Duration"] + flag_cols].copy()

    for c in SPECIAL_SECTIONS:
        if c not in flags_df.columns:
            flags_df[c] = False
        flags_df[c] = flags_df[c].apply(normalise_flag)

    flags_df = flags_df.set_index("Rehearsal")

    # Snap capacity to G
    flags_df["_snap_tokens"] = (flags_df["Duration"].astype(float) // G).astype(int)
    flags_df["_capacity"]    = flags_df["_snap_tokens"] * G

    # Output matrix
    out = works_df.copy()
    for r in all_rehs:
        out[f"Rehearsal {r}"] = 0
    out["Required Minutes"] = req_minutes.astype(int).reindex(out.index).fillna(0).astype(int)

    # --- First & last ---
    first_tokens = int(flags_df.loc[first_r, "_snap_tokens"])
    last_tokens  = int(flags_df.loc[last_r,  "_snap_tokens"])
    first_alloc, last_alloc = bookend_allocations(works_df, first_tokens, last_tokens, G)
    out[f"Rehearsal {first_r}"] = first_alloc.astype(int)
    out[f"Rehearsal {last_r}"]  = last_alloc.astype(int)

    # Remaining requirement per work (for mids)
    remaining_req = (req_minutes - first_alloc - last_alloc).clip(lower=0).astype(int)

    # --- Mids ---
    warnings: List[str] = []
    if mid_rehs and int(remaining_req.sum()) > 0:
        # Attach capacity to flags_df for mid weights
        # (we already set _capacity there)
        allocate_mids(out, works_df, flags_df, mid_rehs, remaining_req, G, warnings)
    else:
        # no mids or nothing remaining; nothing to do
        pass

    # --- Build export + summary rows ---
    used_per_r = {}
    cap_per_r  = {}
    rem_per_r  = {}
    remainder_unsched = {}
    for r in all_rehs:
        used = int(out[f"Rehearsal {r}"].sum())
        cap = int(flags_df.loc[r, "_capacity"])
        rem = cap - used
        used_per_r[r] = used
        cap_per_r[r]  = cap
        rem_per_r[r]  = rem
        remainder_unsched[r] = int(df.set_index("Rehearsal").loc[r, "Duration"]) - cap  # < G sliver that we couldn't schedule

    export = out[["Title", "Required Minutes"] + [f"Rehearsal {r}" for r in all_rehs]].copy()
    export.insert(2, "Time Remaining", "")

    summary_rows = []
    for r in all_rehs:
        row = {
            "Title": f"[Summary] Rehearsal {r}",
            "Required Minutes": int(cap_per_r[r]),
            "Time Remaining": int(rem_per_r[r]),
            f"Rehearsal {r}": int(used_per_r[r]),
        }
        summary_rows.append(row)

    export = pd.concat([export, pd.DataFrame(summary_rows)[export.columns]], ignore_index=True)

    # Warnings: unmet requirements / coverage already appended in mids; also report unschedulable remainders
    for r in all_rehs:
        if remainder_unsched[r] > 0:
            warnings.append(f"[Info] Rehearsal {r}: {remainder_unsched[r]} minute(s) < G were not schedulable due to granularity (G={G}).")

    # Unmet minutes check (should be rare and only if global capacity short)
    unmet = (out[[f"Rehearsal {r}" for r in all_rehs]].sum(axis=1) - out["Required Minutes"]).astype(int)
    for i in out.index:
        if unmet.loc[i] < 0:
            title = str(out.loc[i, "Title"])
            warnings.append(f"[Unmet] '{title}': {-unmet.loc[i]} minute(s) of required time could not be placed.")

    return export, warnings

# =========================
# Main
# =========================
def main():
    parser = argparse.ArgumentParser(description="Allocate rehearsal time: bookends scaled, mids coverage+spread with specialist bias.")
    parser.add_argument("--granularity", type=int, default=DEFAULT_GRANULARITY, help="Rounding granularity in minutes.")
    args = parser.parse_args()

    G = max(1, int(args.granularity))

    works_df = load_works()
    rehearsals_df = load_rehearsals()

    # Compute snapped total capacity (sum of per-rehearsal snap-down)
    tokens_per_reh = (rehearsals_df["Duration"].astype(float) // G).astype(int)
    snapped_capacities = (tokens_per_reh * G).astype(int)
    snapped_total = int(snapped_capacities.sum())
    if snapped_total <= 0:
        raise ValueError("Total rehearsal capacity computed as 0 minutes (after snapping). Check Rehearsals sheet.")

    # Required minutes (tokenized to sum exactly to snapped total)
    req = compute_required_minutes(works_df, snapped_total, G)

    # Allocate
    export_df, warnings = allocate_across_rehearsals(works_df, rehearsals_df, req, G)

    # Save
    export_df.to_excel(OUTPUT_FILE, index=False)
    print(f"Saved: {OUTPUT_FILE}")

    if warnings:
        with open(WARNINGS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(warnings))
        print(f"{len(warnings)} warning(s) written to {WARNINGS_FILE}")

if __name__ == "__main__":
    main()
