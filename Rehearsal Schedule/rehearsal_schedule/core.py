# core.py
import os
import re
import json
import importlib.util
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_G = 5

SCRIPT1 = os.environ.get("SCRIPT1_PATH", "1-Import-Time_per_work_per_rehearsal.py")
SCRIPT2 = os.environ.get("SCRIPT2_PATH", "2-Orchestration_organisation.py")
SCRIPT3 = os.environ.get("SCRIPT3_PATH", "3-Organised_rehearsal_with_time.py")
SCRIPT4 = os.environ.get("SCRIPT4_PATH", "4 - Final Compile and PDF.py")

def load_module_from_path(name: str, path: str):
    # Resolve relative paths to this directory
    if not os.path.isabs(path):
        path = os.path.join(os.path.dirname(__file__), path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing required file: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod

mod1 = load_module_from_path("script1", SCRIPT1)
mod2 = load_module_from_path("script2", SCRIPT2)
mod3 = load_module_from_path("script3", SCRIPT3)

def parse_truthy(x) -> bool:
    if pd.isna(x):
        return False
    s = str(x).strip().upper()
    return s in {"Y","YES","TRUE","T","1"}

def safe_int(x, default=0) -> int:
    try:
        if pd.isna(x):
            return default
        return int(float(x))
    except Exception:
        return default

def minutes_from_timecell(val):
    if pd.isna(val):
        return None
    if hasattr(val, "hour") and hasattr(val, "minute"):
        try:
            return int(val.hour)*60 + int(val.minute)
        except Exception:
            pass
    s = str(val).strip().replace("：", ":")
    if re.match(r"^\d{1,2}\.\d{2}(\s*(AM|PM|am|pm))?$", s):
        s = s.replace(".", ":")
    m = re.match(r"^\s*(\d{1,2}):(\d{2})", s)
    if m:
        hh = int(m.group(1)) % 24
        mm = int(m.group(2))
        if re.search(r"pm$", s, flags=re.I) and hh < 12:
            hh += 12
        if re.search(r"am$", s, flags=re.I) and hh == 12:
            hh = 0
        return hh*60 + mm
    m = re.fullmatch(r"^(\d{3,4})$", s)
    if m:
        v = int(m.group(1))
        hh = v // 100
        mm = v % 100
        if 0 <= hh < 24 and 0 <= mm < 60:
            return hh*60 + mm
    try:
        f = float(s)
        if 0 <= f < 1:
            return int(round(f * 24 * 60))
        if 1 <= f < 24*60 + 1:
            return int(round(f))
    except Exception:
        pass
    t = pd.to_datetime(s, errors="coerce")
    if pd.notna(t):
        return int(t.hour)*60 + int(t.minute)
    return None

def parse_break_minutes(val) -> int:
    if pd.isna(val):
        return 0
    num = pd.to_numeric(val, errors="coerce")
    if pd.notna(num):
        return int(round(float(num)))
    t = pd.to_datetime(val, errors="coerce")
    if pd.notna(t):
        return int(t.hour)*60 + int(t.minute)
    s = str(val).strip()
    if ":" in s:
        p = s.split(":")
        if len(p)==2 and p[0].isdigit() and p[1].isdigit():
            return int(p[0])*60 + int(p[1])
    return 0

def prepare_works_df(works: pd.DataFrame) -> pd.DataFrame:
    w = works.copy()
    w["Title"] = w["Title"].astype(str).str.strip()
    w = w[w["Title"].str.len() > 0].copy()
    w["Duration"] = pd.to_numeric(w["Duration"], errors="coerce").fillna(0.0)
    w["Difficulty"] = pd.to_numeric(w["Difficulty"], errors="coerce").fillna(1.0)

    # Use your script1 normaliser if present
    try:
        w2 = mod1.normalise_works_columns(w)
    except Exception:
        w2 = w.copy()
        w2["duration_norm"] = w2["Duration"].astype(float)
        w2["difficulty_norm"] = w2["Difficulty"].astype(float).clip(lower=0.1)

    # Ensure orchestration cols exist
    for c in (
        mod1.WIND_COLS + mod1.BRASS_COLS + mod1.STRING_COLS +
        mod1.PERC_COLS + mod1.PIANO_COLS + mod1.HARP_COLS + mod1.SOLOIST_COLS
    ):
        if c not in w2.columns:
            w2[c] = 0
    return w2.reset_index(drop=True)

def prepare_rehearsals_df(rehearsals: pd.DataFrame) -> pd.DataFrame:
    r = rehearsals.copy()
    r = r[r["Rehearsal"].astype(str).str.strip().ne("")].copy()
    r["Rehearsal"] = pd.to_numeric(r["Rehearsal"], errors="coerce").astype("Int64")
    r = r[r["Rehearsal"].notna()].copy()
    r["Date"] = pd.to_datetime(r.get("Date"), errors="coerce").dt.date

    start_min = r.get("Start Time").apply(minutes_from_timecell) if "Start Time" in r.columns else None
    end_min = r.get("End Time").apply(minutes_from_timecell) if "End Time" in r.columns else None
    if start_min is None:
        start_min = pd.Series([19*60]*len(r))
    if end_min is None:
        end_min = pd.Series([21*60+30]*len(r))

    start_min = start_min.fillna(19*60).astype(int)
    end_min = end_min.fillna(21*60+30).astype(int)

    gross = (end_min - start_min).astype(int)
    gross = gross.where(gross >= 0, gross + 24*60)

    br = r.get("Break").apply(parse_break_minutes) if "Break" in r.columns else pd.Series([0]*len(r))
    r["Break (minutes)"] = br.astype(int)
    r["Duration"] = (gross - r["Break (minutes)"]).clip(lower=0).astype(int)

    for c in ["Percs","Piano","Harp","Brass","Soloist"]:
        if c not in r.columns:
            r[c] = False
        r[c] = r[c].apply(parse_truthy)

    hh = (start_min // 60).astype(int).astype(str).str.zfill(2)
    mm = (start_min % 60).astype(int).astype(str).str.zfill(2)
    hhmm = hh + ":" + mm
    date_str = np.where(pd.notna(pd.Series(r["Date"])), pd.Series(r["Date"]).astype(str), "2000-01-01")
    r["Start DateTime"] = pd.to_datetime(date_str + " " + hhmm, errors="coerce").fillna(pd.to_datetime("2000-01-01 19:00"))

    return r.sort_values("Rehearsal").reset_index(drop=True)

# ---- Ordering logic (bundles/movements together + descending load + similarity)
@dataclass
class Bundle:
    key: str
    items: pd.DataFrame
    mins: int
    playerload: float
    sig: Dict[str, int]

def build_signature_map(works_df: pd.DataFrame) -> Dict[str, Dict[str, int]]:
    sig_map: Dict[str, Dict[str, int]] = {}
    for _, r in works_df.iterrows():
        title = str(r.get("Title","")).strip()
        if not title:
            continue
        try:
            sig = mod3.signature_for_work(r)
        except Exception:
            sig = {"Percs":0,"PercProfile":0,"Piano":0,"Harp":0,"Winds":0,"Brass":0,"Strings":0}
        sig_map[title] = {k:int(sig.get(k,0)) for k in ["Percs","PercProfile","Piano","Harp","Winds","Brass","Strings"]}
    return sig_map

def estimate_playerload_map(works_df: pd.DataFrame) -> Dict[str, float]:
    groups = mod2.gather_resolved_groups(works_df)
    out = {}
    works_indexed = works_df.set_index("Title", drop=False)
    for t, wr in works_indexed.groupby(level=0):
        row = wr.iloc[0]
        out[str(t)] = float(mod2.estimate_player_load(row, groups))
    return out

def parse_group_and_movement(title: str, group_hint: Optional[str]=None):
    return mod2.parse_group_and_movement(title, group_hint)

def build_bundles_for_rehearsal(schedule_df: pd.DataFrame, sig_map: Dict[str, Dict[str, int]]) -> List[Bundle]:
    bundles: List[Bundle] = []
    for gk, grp in schedule_df.groupby("GroupKey", sort=False):
        grp2 = grp.copy()
        if "MovementOrder" in grp2.columns:
            grp2["MovementOrder"] = pd.to_numeric(grp2["MovementOrder"], errors="coerce")
            grp2 = grp2.sort_values(["MovementOrder","Title"], na_position="last")
        mins = int(pd.to_numeric(grp2["Rehearsal Time (minutes)"], errors="coerce").fillna(0).sum())
        playerload = float(pd.to_numeric(grp2["PlayerLoad"], errors="coerce").fillna(0).max())
        sig = {"Percs":0,"PercProfile":0,"Piano":0,"Harp":0,"Winds":0,"Brass":0,"Strings":0}
        for t in grp2["Title"].astype(str).tolist():
            s = sig_map.get(t)
            if not s:
                continue
            for k in sig.keys():
                sig[k] = max(int(sig[k]), int(s.get(k,0)))
        bundles.append(Bundle(key=str(gk), items=grp2, mins=mins, playerload=playerload, sig=sig))
    return bundles

def order_bundles(bundles: List[Bundle], increase_penalty_weight: float=100.0) -> List[Bundle]:
    if not bundles:
        return []
    remaining = bundles[:]
    remaining.sort(key=lambda b: (b.playerload, b.mins), reverse=True)
    ordered = [remaining.pop(0)]
    while remaining:
        last = ordered[-1]
        last_load = last.playerload
        best_i = 0
        best_key = None
        for i, cand in enumerate(remaining):
            inc = max(0.0, cand.playerload - last_load)
            inc_pen = inc * increase_penalty_weight
            tc = mod3.transition_cost(last.sig, cand.sig)
            key = (inc_pen, tc, -cand.playerload, -cand.mins)
            if best_key is None or key < best_key:
                best_key = key
                best_i = i
        ordered.append(remaining.pop(best_i))
    return ordered

# ---- Break choice (favor longer first half when not equal)
def choose_break_offset_favor_longer_first_half(durations: List[int]) -> int:
    if not durations:
        return 0
    boundaries = [0]
    for m in durations:
        boundaries.append(boundaries[-1] + int(m))
    total = boundaries[-1]
    if len(boundaries) <= 2:
        return 0
    ideal = total / 2.0
    candidates = range(1, len(boundaries) - 1)
    def key(i: int):
        b = boundaries[i]
        return (abs(2*b - total), 0 if b >= ideal else 1, -b)
    best_idx = min(candidates, key=key)
    return int(boundaries[best_idx])

def hhmm(dt) -> str:
    return pd.to_datetime(dt).strftime("%H:%M")

def compute_timed_df(schedule_df: pd.DataFrame, rehearsals_prepared: pd.DataFrame) -> pd.DataFrame:
    if schedule_df.empty:
        return pd.DataFrame()

    rehe = rehearsals_prepared.set_index("Rehearsal", drop=False)
    out_rows = []

    for rnum, df_r in schedule_df.groupby("Rehearsal", sort=True):
        rnum_i = int(rnum)
        if rnum_i not in rehe.index:
            continue

        rrow = rehe.loc[rnum_i]
        date = rrow.get("Date")
        start_dt = pd.to_datetime(rrow.get("Start DateTime"), errors="coerce")
        if pd.isna(start_dt):
            start_dt = pd.to_datetime("2000-01-01 19:00")

        break_mins = safe_int(rrow.get("Break (minutes)", 0), 0)
        durs = [safe_int(x, 0) for x in df_r["Rehearsal Time (minutes)"].tolist()]
        durs = [d for d in durs if d > 0]

        break_offset = 0
        if break_mins > 0 and len(durs) >= 2:
            break_offset = choose_break_offset_favor_longer_first_half(durs)

        elapsed = 0
        for _, item in df_r.iterrows():
            mins = safe_int(item["Rehearsal Time (minutes)"], 0)
            if mins <= 0:
                continue

            if break_mins > 0 and break_offset > 0 and elapsed == break_offset:
                br_start = start_dt + pd.Timedelta(minutes=elapsed)
                br_end = br_start + pd.Timedelta(minutes=break_mins)
                out_rows.append({
                    "Rehearsal": rnum_i,
                    "Date": date,
                    "Title": "Break",
                    "Time in Rehearsal": hhmm(br_start),
                    "Break Start (HH:MM)": hhmm(br_start),
                    "Break End (HH:MM)": hhmm(br_end),
                })
                elapsed += break_mins

            it_start = start_dt + pd.Timedelta(minutes=elapsed)
            out_rows.append({
                "Rehearsal": rnum_i,
                "Date": date,
                "Title": str(item["Title"]),
                "Time in Rehearsal": hhmm(it_start),
                "Break Start (HH:MM)": "",
                "Break End (HH:MM)": "",
            })
            elapsed += mins

    out = pd.DataFrame(out_rows)
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out["Rehearsal"] = pd.to_numeric(out["Rehearsal"], errors="coerce").astype("Int64")
    return out

# ---- High-level operations
def run_allocation(works_df: pd.DataFrame, rehearsals_df: pd.DataFrame, G: int = DEFAULT_G):
    works = prepare_works_df(works_df)
    rehe = prepare_rehearsals_df(rehearsals_df)

    tokens_per = (rehe["Duration"].astype(float) // G).astype(int)
    snapped_caps = (tokens_per * G).astype(int)
    snapped_total = int(snapped_caps.sum())

    req = mod1.compute_required_minutes(works, snapped_total, G)
    export_df, warnings = mod1.allocate_across_rehearsals(works, rehe, req, G)
    return export_df, list(warnings or [])

def generate_schedule_from_allocation(allocation_df: pd.DataFrame, works_df: pd.DataFrame):
    works = prepare_works_df(works_df)

    alloc = allocation_df.copy()
    alloc = alloc[~alloc["Title"].astype(str).str.startswith("[Summary]")].copy()

    reh_cols = [c for c in alloc.columns if str(c).strip().startswith("Rehearsal ")]
    col_to_num = {}
    for c in reh_cols:
        try:
            col_to_num[c] = int(str(c).split()[-1])
        except Exception:
            pass
    if not col_to_num:
        raise ValueError("No 'Rehearsal N' columns found in allocation output.")

    playerload_map = estimate_playerload_map(works)
    sig_map = build_signature_map(works)

    # group hints if present
    try:
        group_col = mod2._first_matching_col(works, mod2.GROUP_ALIASES)
    except Exception:
        group_col = None
    group_hint_map = {}
    if group_col and group_col in works.columns:
        for _, r in works.iterrows():
            t = str(r.get("Title","")).strip()
            if t:
                v = r.get(group_col)
                group_hint_map[t] = str(v).strip() if pd.notna(v) and str(v).strip() else None

    rows = []
    for _, r in alloc.iterrows():
        title = str(r.get("Title","")).strip()
        if not title:
            continue
        for col, rnum in col_to_num.items():
            mins = pd.to_numeric(r.get(col, 0), errors="coerce")
            if pd.isna(mins) or mins <= 0:
                continue
            group_hint = group_hint_map.get(title)
            group_title, mov_label, mov_ord = parse_group_and_movement(title, group_hint)
            rows.append({
                "Rehearsal": int(rnum),
                "Title": title,
                "Rehearsal Time (minutes)": int(round(float(mins))),
                "PlayerLoad": float(playerload_map.get(title, 0.0)),
                "GroupKey": str(group_title),
                "MovementOrder": mov_ord if mov_ord is not None else np.nan,
            })

    sched = pd.DataFrame(rows)
    sched["Rehearsal"] = pd.to_numeric(sched["Rehearsal"], errors="coerce").astype("Int64")
    sched["MovementOrder"] = pd.to_numeric(sched["MovementOrder"], errors="coerce")

    ordered_rows = []
    for rnum, df_r in sched.groupby("Rehearsal", sort=True):
        bundles = build_bundles_for_rehearsal(df_r, sig_map)
        ordered_bundles = order_bundles(bundles, increase_penalty_weight=100.0)
        for b in ordered_bundles:
            ordered_rows.append(b.items)

    return pd.concat(ordered_rows, ignore_index=True)
