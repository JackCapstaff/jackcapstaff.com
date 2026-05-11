# 3-Organised_rehearsal_with_time.py  — fixes: robust start time parsing + central break on internal boundary
import argparse
import re
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from collections import defaultdict

# =========================
# Config
# =========================
INPUT_FILE = "User_Input_1.xlsx"
SHEET_REHEARSALS = "Rehearsals"
SHEET_WORKS = "Works"

SCHEDULE_FILE = "Rehearsal_schedule.xlsx"   # from script 2
OUTPUT_FILE = "timed_rehearsal.xlsx"

# Guardrails for break placement
MIN_BEFORE_AFTER = 25      # keep >= this many mins of work before/after break where feasible
MICRO_ITEM = 4             # avoid isolating a tiny (<4') item next to the break if there is a comparable alternative

# Orchestration columns (for signatures)
WIND_COLS   = ["Flute", "Oboe", "Clarinet", "Bassoon", "Piccolo", "Cor Anglais", "CorAnglais", "Saxophone"]
BRASS_COLS  = ["Horn", "Trumpet", "Trombone", "Tuba"]
STRING_COLS = ["Violin 1", "Violin 2", "Viola", "Cello", "Bass", "Double Bass"]
PERC_COLS   = ["Percussion", "Timpani"]
PIANO_COLS  = ["Piano", "Celeste", "Celesta", "Keyboard"]
HARP_COLS   = ["Harp"]

# =========================
# Small helper: robust cell→minutes (same spirit as script 1)
# =========================
from datetime import time as _pytime

def _to_minutes(value):
    if pd.isna(value):
        return np.nan

    # Python datetime.time (common when Excel cell is read as pure time)
    if isinstance(value, _pytime):
        return int(value.hour) * 60 + int(value.minute)

    # Pandas/NumPy time-like scalars (safe cast to string first)
    if hasattr(value, "hour") and hasattr(value, "minute"):
        try:
            return int(value.hour) * 60 + int(value.minute)
        except Exception:
            pass

    # Strings: "19:15", "7:15 PM", "19.15", "19 15"
    if isinstance(value, str):
        s = value.strip()
        if re.match(r"^\d{1,2}\s+\d{2}(\s*(AM|PM|am|pm))?$", s):
            s = s.replace(" ", ":")  # "19 15" -> "19:15"
        if re.match(r"^\d{1,2}\.\d{2}(\s*(AM|PM|am|pm))?$", s):
            s = s.replace(".", ":")  # "19.15" -> "19:15"

        if ":" in s:
            hhmm = re.match(r"^\s*(\d{1,2}):(\d{2})", s)
            if hhmm:
                hh = int(hhmm.group(1)) % 24
                mm = int(hhmm.group(2))
                if re.search(r"pm$", s, flags=re.I) and hh < 12:
                    hh += 12
                if re.search(r"am$", s, flags=re.I) and hh == 12:
                    hh = 0
                return hh * 60 + mm

        # final string fallback via pandas parser
        t = pd.to_datetime(s, errors="coerce")
        if pd.notna(t):
            return int(t.hour) * 60 + int(t.minute)
        return np.nan

    # Numeric: Excel fraction of a day or raw minutes
    try:
        f = float(value)
        if 0 <= f < 1:             # Excel fraction of a day
            return int(round(f * 24 * 60))
        if 1 <= f < 24 * 60 + 1:   # already minutes
            return int(round(f))
    except Exception:
        pass

    # Datetime-like
    t = pd.to_datetime(value, errors="coerce")
    if pd.notna(t):
        return int(t.hour) * 60 + int(t.minute)

    return np.nan


# =========================
# Helpers: IO
# =========================
def load_rehearsals() -> pd.DataFrame:
    df = pd.read_excel(INPUT_FILE, sheet_name=SHEET_REHEARSALS)
    if "Rehearsal" not in df.columns:
        raise ValueError("Rehearsals sheet must contain a 'Rehearsal' column")
    df["Rehearsal"] = pd.to_numeric(df["Rehearsal"], errors="coerce").astype("Int64")

    # Date
    df["Date"] = pd.to_datetime(df.get("Date"), errors="coerce").dt.date

    # Robust Start/End → minutes
    # Robust Start/End → minutes
    start_m = df.get("Start Time")
    end_m   = df.get("End Time")
    start_mins = start_m.apply(_to_minutes) if start_m is not None else pd.Series(np.nan, index=df.index)
    end_mins   = end_m.apply(_to_minutes)   if end_m   is not None else pd.Series(np.nan, index=df.index)

    # Sensible defaults only when missing
    start_mins = pd.to_numeric(start_mins, errors="coerce").fillna(19 * 60).astype(int)
    end_mins   = pd.to_numeric(end_mins,   errors="coerce").fillna(21 * 60 + 30).astype(int)

    # Break minutes
    br_num = pd.to_numeric(df.get("Break"), errors="coerce")
    if br_num is None or br_num.isna().all():
        br_time = pd.to_datetime(df.get("Break"), errors="coerce")
        br_num = (br_time.dt.hour * 60 + br_time.dt.minute).astype("float")
    br_num = br_num.fillna(0.0).astype(int)
    df["Break (minutes)"] = br_num

    # Start DateTime from parsed minutes
    hh = (start_mins // 60).astype(int).astype(str).str.zfill(2)
    mm = (start_mins % 60).astype(int).astype(str).str.zfill(2)
    hhmm = hh + ":" + mm
    date_str = np.where(pd.notna(df["Date"]), df["Date"].astype(str), "2000-01-01")
    df["Start DateTime"] = pd.to_datetime(date_str + " " + hhmm, errors="coerce")
    df["Start DateTime"] = df["Start DateTime"].fillna(pd.to_datetime("2000-01-01 19:00"))

    # Duration (gross minus break), Series-safe
    gross = pd.to_numeric(end_mins - start_mins, errors="coerce").fillna(0.0)
    gross = gross.where(gross >= 0, gross + 24 * 60)  # across midnight
    df["Duration"] = (gross - df["Break (minutes)"]).clip(lower=0.0)


    return df

def load_schedule() -> pd.DataFrame:
    df = pd.read_excel(SCHEDULE_FILE)
    if "Rehearsal" not in df.columns or "Title" not in df.columns:
        raise ValueError("Rehearsal_schedule.xlsx must contain 'Rehearsal' and 'Title' columns.")
    # minutes column
    if "Rehearsal Time (minutes)" not in df.columns and "Rehearsal Time" in df.columns:
        df["Rehearsal Time (minutes)"] = pd.to_numeric(df["Rehearsal Time"], errors="coerce").fillna(0).astype(int)
    elif "Rehearsal Time (minutes)" in df.columns:
        df["Rehearsal Time (minutes)"] = pd.to_numeric(df["Rehearsal Time (minutes)"], errors="coerce").fillna(0).astype(int)
    else:
        raise ValueError("Rehearsal_schedule.xlsx must contain 'Rehearsal Time (minutes)' (or 'Rehearsal Time').")
    df["Rehearsal"] = pd.to_numeric(df["Rehearsal"], errors="coerce").astype("Int64")

    # Optional columns from script 2 (if absent, fall back gracefully)
    if "PlayerLoad" not in df.columns:   df["PlayerLoad"] = 0
    if "GroupKey" not in df.columns:     df["GroupKey"] = df["Title"]
    if "MovementOrder" not in df.columns:df["MovementOrder"] = np.nan

    return df[["Rehearsal","Title","Rehearsal Time (minutes)","PlayerLoad","GroupKey","MovementOrder"]]

def load_works() -> pd.DataFrame:
    return pd.read_excel(INPUT_FILE, sheet_name=SHEET_WORKS)

# =========================
# Signatures for transition cost
# =========================
def any_positive(row: pd.Series, cols: List[str]) -> bool:
    for c in cols:
        if c in row.index:
            try:
                if pd.to_numeric(row[c], errors="coerce") > 0:
                    return True
            except Exception:
                pass
    return False

def signature_for_work(row: pd.Series) -> Dict[str, int]:
    sig = {
        "Percs":   int(any_positive(row, PERC_COLS)),
        "Piano":   int(any_positive(row, PIANO_COLS)),
        "Harp":    int(any_positive(row, HARP_COLS)),
        "Winds":   int(any_positive(row, WIND_COLS)),
        "Brass":   int(any_positive(row, BRASS_COLS)),
        "Strings": int(any_positive(row, STRING_COLS)),
    }
    # crude perc profile (0 none, 1 light, 2 heavy)
    perc_count = 0
    for c in PERC_COLS:
        if c in row.index:
            try:
                v = pd.to_numeric(row[c], errors="coerce")
                if pd.notna(v) and v > 0: perc_count += int(v)
            except Exception: pass
    sig["PercProfile"] = 0 if sig["Percs"] == 0 else (1 if perc_count <= 2 else 2)
    return sig

def build_signature_table(works_df: pd.DataFrame) -> pd.DataFrame:
    cols = ["Title","Percs","PercProfile","Piano","Harp","Winds","Brass","Strings"]
    out = []
    for _, r in works_df.iterrows():
        out.append({"Title": r.get("Title"), **signature_for_work(r)})
    sig_df = pd.DataFrame(out)
    return sig_df[cols].drop_duplicates(subset=["Title"], keep="first")

# =========================
# Ordering heuristic (bundles = groups)
# =========================
def transition_cost(a: Dict[str, int], b: Dict[str, int]) -> int:
    cost = 0
    cost += 3 if a["Percs"] != b["Percs"] else 0
    if a["Percs"] and b["Percs"] and a["PercProfile"] != b["PercProfile"]: cost += 2
    cost += 2 if a["Piano"] != b["Piano"] else 0
    cost += 2 if a["Harp"]  != b["Harp"]  else 0
    cost += 1 if a["Winds"] != b["Winds"] else 0
    cost += 1 if a["Brass"] != b["Brass"] else 0
    cost += 1 if a["Strings"] != b["Strings"] else 0
    return cost

def pick_seed_bundle(bundles: List[Dict]) -> int:
    # start with the largest headcount; tiebreak by longer duration
    best_i, best_key = 0, (-1, -1)
    for i, b in enumerate(bundles):
        key = (b.get("PlayerLoad", 0), b["mins"])
        if key > best_key:
            best_i, best_key = i, key
    return best_i

def greedy_order_bundles(bundles: List[Dict]) -> List[Dict]:
    if not bundles: return bundles
    items = bundles.copy()
    order = [items.pop(pick_seed_bundle(items))]
    while items:
        last = order[-1]
        # choose next with min transition cost; prefer higher PlayerLoad and longer minutes
        best_j, best_tuple = 0, (9999, -1, -1)
        for j, cand in enumerate(items):
            c = transition_cost(last["sig"], cand["sig"])
            tup = (c, cand.get("PlayerLoad", 0), cand["mins"])
            if (c, -cand.get("PlayerLoad", 0), -cand["mins"]) < (best_tuple[0], -best_tuple[1], -best_tuple[2]):
                best_j, best_tuple = j, tup
        order.append(items.pop(best_j))
    # small local improvement pass
    def total_cost(seq): return sum(transition_cost(seq[k]["sig"], seq[k+1]["sig"]) for k in range(len(seq)-1))
    improved = True
    while improved and len(order) >= 3:
        improved = False
        for k in range(len(order)-1):
            cur = total_cost(order)
            swap = order.copy(); swap[k], swap[k+1] = swap[k+1], swap[k]
            if total_cost(swap) + 1e-9 < cur:
                order = swap; improved = True
    return order

# =========================
# Break placement (internal boundaries only)
# =========================
def choose_break_boundary(durations: list[int],
                          min_before_after: int = MIN_BEFORE_AFTER,
                          micro_item: int = MICRO_ITEM) -> int:
    """
    Choose a break offset (minutes) strictly at an internal boundary between items,
    minimizing |left_work - right_work|. If two boundaries are equally close to the halfway
    point, prefer the EARLIER boundary (keeps the break from drifting late).
    """
    if not durations:
        return 0

    # cumulative boundaries BEFORE each item (0 .. total)
    boundaries = [0]
    for m in durations:
        boundaries.append(boundaries[-1] + m)
    total = boundaries[-1]

    # no internal boundary → don't split the single item
    if len(boundaries) <= 2:
        return 0

    ideal = total / 2.0

    # INTERNAL boundaries only
    candidates = range(1, len(boundaries) - 1)

    # primary objective: closest to ideal (i.e., minimize |2*b - total|)
    # secondary: prefer earlier (b <= ideal) when tied
    def key(i: int):
        b = boundaries[i]
        return (abs(2*b - total), 0 if b <= ideal else 1, b)

    best_idx = min(candidates, key=key)
    return int(boundaries[best_idx])



# =========================
# Build per-rehearsal timeline
# =========================
def compute_timeline_for_rehearsal(
    reh_num: int,
    start_dt: pd.Timestamp,
    break_mins: int,
    items: List[Dict]
) -> pd.DataFrame:
    """
    items: list of dicts with keys: Title, mins, sig, playerload, group, move
    Returns ordered rows + a single Break row, with start times.
    """
    if not items:
        return pd.DataFrame(columns=[
            "Rehearsal","Date","Start DateTime","Title","Rehearsal Time",
            "Rehearsal Time (minutes)","Time in Rehearsal","Break (minutes)",
            "Break Start","Break End"
        ])

    # Bundles by group
    groups = defaultdict(list)
    for it in items:
        groups[it["group"]].append(it)

    bundles = []
    for gk, members in groups.items():
        # within a group: order by MovementOrder if given, else Title
        if any(pd.notna(x.get("move")) for x in members):
            members = sorted(members, key=lambda x: (pd.isna(x.get("move")), x.get("move"), x["Title"]))
        else:
            members = sorted(members, key=lambda x: x["Title"])
        # aggregate signature across members; accumulate minutes & playerload
        sig = {"Percs":0,"PercProfile":0,"Piano":0,"Harp":0,"Winds":0,"Brass":0,"Strings":0}
        mins_sum, pload_sum = 0, 0
        for m in members:
            mins_sum += m["mins"]
            pload_sum += (m.get("playerload") or 0)
            for k in ["Percs","Piano","Harp","Winds","Brass","Strings"]:
                sig[k] = int(sig[k] or m["sig"].get(k, 0))
            sig["PercProfile"] = max(sig["PercProfile"], m["sig"].get("PercProfile", 0))
        bundles.append({"group": gk, "members": members, "mins": int(mins_sum), "sig": sig, "PlayerLoad": int(pload_sum)})

    # Order bundles (largest players earlier + low transition cost)
    ordered_bundles = greedy_order_bundles(bundles)

    # Flatten in that bundle order
    ordered_items = []
    for b in ordered_bundles:
        ordered_items.extend(b["members"])

    # Durations and internal-boundary break
    durs = [it["mins"] for it in ordered_items]
    break_offset = choose_break_boundary(durs)

    # Build time deltas (shift items after break by break_mins)
    cum_before = np.cumsum([0] + durs[:-1]).astype(int)
    shifted = [off if off < break_offset else off + break_mins for off in cum_before]

    rows = []
    for it, off in zip(ordered_items, shifted):
        rows.append({
            "Rehearsal": reh_num,
            "Date": start_dt.date(),
            "Start DateTime": start_dt,
            "Title": it["Title"],
            "Rehearsal Time": it["mins"],
            "Rehearsal Time (minutes)": it["mins"],
            "Time Delta (minutes)": off,
            "Time Delta": pd.to_timedelta(off, unit="m"),
            "Time in Rehearsal": (start_dt + pd.to_timedelta(off, unit="m")).strftime("%H:%M"),
            "Break (minutes)": break_mins
        })

    out = pd.DataFrame(rows)

    # Insert Break row at chosen offset
    if break_mins > 0 and len(durs) >= 1:
        br_start = start_dt + pd.to_timedelta(break_offset, unit="m")
        br_end   = br_start + pd.to_timedelta(break_mins, unit="m")
        break_row = {
            "Rehearsal": reh_num,
            "Date": start_dt.date(),
            "Start DateTime": start_dt,
            "Title": "Break",
            "Rehearsal Time": break_mins,
            "Rehearsal Time (minutes)": break_mins,
            "Time Delta (minutes)": break_offset,
            "Time Delta": pd.to_timedelta(break_offset, unit="m"),
            "Time in Rehearsal": br_start.strftime("%H:%M"),
            "Break (minutes)": break_mins,
            "Break Start": br_start,
            "Break End": br_end
        }
        out = pd.concat([out, pd.DataFrame([break_row])], ignore_index=True)

    # Order by time within the rehearsal
    out = out.sort_values(by=["Time Delta (minutes)", "Title"], kind="mergesort").reset_index(drop=True)

    # Pretty break strings
    if "Break Start" not in out.columns: out["Break Start"] = pd.NaT
    if "Break End"   not in out.columns: out["Break End"]   = pd.NaT
    out["Break Start (HH:MM)"] = pd.to_datetime(out["Break Start"], errors="coerce").dt.strftime("%H:%M")
    out["Break End (HH:MM)"]   = pd.to_datetime(out["Break End"],   errors="coerce").dt.strftime("%H:%M")

    return out

# =========================
# Main
# =========================
def main():
    parser = argparse.ArgumentParser(description="Order grouped pieces per rehearsal; put larger ensembles earlier; place a single central break (never inside an item).")
    args = parser.parse_args()

    rehearsals_df = load_rehearsals()
    schedule_df   = load_schedule()
    works_df      = load_works()

    # signatures by Title
    sig_df = build_signature_table(works_df)

    # join signatures onto schedule
    sched = schedule_df.merge(sig_df, on="Title", how="left")

    # map rehearsal → start_dt, break_mins
    start_map = rehearsals_df.set_index("Rehearsal")["Start DateTime"].to_dict()
    break_map = rehearsals_df.set_index("Rehearsal")["Break (minutes)"].to_dict()

    # Build per-rehearsal output
    out_blocks = []
    for reh_num, group in sched.groupby("Rehearsal", sort=True):
        if pd.isna(reh_num): continue
        reh_num = int(reh_num)
        start_dt = start_map.get(reh_num, pd.to_datetime("2000-01-01 19:00"))
        br_mins  = int(break_map.get(reh_num, 0))

        # items = [{Title, mins, sig, playerload, group, move}]
        items = []
        for _, r in group.iterrows():
            mins = int(pd.to_numeric(r["Rehearsal Time (minutes)"], errors="coerce") or 0)
            if mins <= 0: continue
            sig = {
                "Percs": int(r.get("Percs", 0) or 0),
                "PercProfile": int(r.get("PercProfile", 0) or 0),
                "Piano": int(r.get("Piano", 0) or 0),
                "Harp": int(r.get("Harp", 0) or 0),
                "Winds": int(r.get("Winds", 0) or 0),
                "Brass": int(r.get("Brass", 0) or 0),
                "Strings": int(r.get("Strings", 1) or 0),
            }
            items.append({
                "Title": str(r["Title"]),
                "mins": mins,
                "sig": sig,
                "playerload": int(pd.to_numeric(r.get("PlayerLoad"), errors="coerce")) if pd.notna(r.get("PlayerLoad")) else 0,
                "group": str(r.get("GroupKey")) if pd.notna(r.get("GroupKey")) else str(r["Title"]),
                "move": int(pd.to_numeric(r.get("MovementOrder"), errors="coerce")) if pd.notna(r.get("MovementOrder")) else None,
            })

        out_blocks.append(compute_timeline_for_rehearsal(reh_num, start_dt, br_mins, items))

    final = pd.concat(out_blocks, ignore_index=True) if out_blocks else pd.DataFrame(columns=[
        "Rehearsal","Date","Start DateTime","Title","Rehearsal Time","Rehearsal Time (minutes)",
        "Time Delta (minutes)","Time Delta","Time in Rehearsal","Break (minutes)",
        "Break Start","Break End","Break Start (HH:MM)","Break End (HH:MM)"
    ])

    # Tidy columns
    out_cols = [
        "Rehearsal","Date","Start DateTime","Title","Rehearsal Time","Rehearsal Time (minutes)",
        "Time in Rehearsal","Break (minutes)","Break Start (HH:MM)","Break End (HH:MM)"
    ]
    final_out = final[[c for c in out_cols if c in final.columns]].copy()
    final_out.rename(columns={"Start DateTime": "Rehearsal Start"}, inplace=True)

    final_out.to_excel(OUTPUT_FILE, index=False)
    print(f"Saved: {OUTPUT_FILE} ({len(final_out)} rows)")

if __name__ == "__main__":
    main()
