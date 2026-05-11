import pandas as pd
import tkinter as tk
from tkinter import filedialog
from pathlib import Path

# --- CONFIG ---
MAX_L, MAX_W, MAX_H = 60.0, 40.0, 40.0
WEIGHT_THRESHOLD = 7.0


def select_csv_file():
    root = tk.Tk()
    root.withdraw()  # Hide the empty window
    file_path = filedialog.askopenfilename(
        title="Select orders CSV",
        filetypes=[("CSV files", "*.csv")]
    )
    return file_path


def box_exceeds_limit(row):
    l, w, h = row["length"], row["width"], row["height"]
    if pd.isna(l) or pd.isna(w) or pd.isna(h):
        return False
    return (l > MAX_L) or (w > MAX_W) or (h > MAX_H)


def classify_inventory_group(inv_df):
    # Any box too large => Location 1
    if inv_df["__box_exceeds"].any():
        return 1

    # Any complete dimensions present => Location 2
    has_complete_dims = inv_df[["length", "width", "height"]].notna().all(axis=1).any()
    if has_complete_dims:
        return 2

    # Fallback to shipping weight
    sw = inv_df["shippingWeight"].dropna()
    if sw.empty:
        return 1  # safe default

    return 2 if sw.max() < WEIGHT_THRESHOLD else 1


def classify_order(order_df):
    locs = set(order_df["item_location"].unique())
    if locs == {1}:
        return "L1"
    if locs == {2}:
        return "L2"
    return "Split"


def main():
    csv_path = select_csv_file()
    if not csv_path:
        print("No file selected — exiting.")
        return

    df = pd.read_csv(csv_path)

    # Ensure numeric columns
    for col in ["shippingWeight", "actualWeight", "quantity", "length", "width", "height", "weight"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Per-box size check
    df["__box_exceeds"] = df.apply(box_exceeds_limit, axis=1)

    # Per SKU / inventoryId classification
    item_loc = (
        df.groupby("inventoryId", dropna=False)
          .apply(classify_inventory_group)
          .rename("item_location")
          .reset_index()
    )

    df = df.merge(item_loc, on="inventoryId", how="left")

    # Per-order classification
    order_class = (
        df.groupby("orderNumber", dropna=False)
          .apply(classify_order)
          .rename("dispatch_class")
          .reset_index()
    )

    # Summary counts
    total_orders = order_class["orderNumber"].nunique()
    l1_only = (order_class["dispatch_class"] == "L1").sum()
    l2_only = (order_class["dispatch_class"] == "L2").sum()
    split = (order_class["dispatch_class"] == "Split").sum()

    print("\n=== Dispatch Summary (unique orders) ===")
    print(f"Total unique orders:           {total_orders}")
    print(f"Dispatch Location 1 only:      {l1_only}")
    print(f"Dispatch Location 2 only:      {l2_only}")
    print(f"Split (both locations):        {split}")
    print(f"Total dispatch location 1:     {l1_only + split}")
    print(f"Total dispatch location 2:     {l2_only + split}")

    # Save output next to source file
    output_path = Path(csv_path).with_name("order_dispatch_summary.csv")
    order_class.to_csv(output_path, index=False)
    print(f"\nSaved order-level results to:\n{output_path}")


if __name__ == "__main__":
    main()
