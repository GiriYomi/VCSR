#!/usr/bin/env python3
"""
download_tgb.py — Download TGB datasets and convert to DGAP edge-list format.

Usage:
    python download_tgb.py                              # default datasets
    python download_tgb.py --datasets tgbl-wiki tgbl-coin
    python download_tgb.py --base-frac 0.1              # 10% base (default)
    python download_tgb.py --list                        # list available datasets

Output per dataset:
    data/<name>.base.el      — base graph (first 10% + node padding)
    data/<name>.dynamic.el   — dynamic edges (remaining 90%)

Note: This script removes self-loops before dedup. For processing WITHOUT
self-loop removal (matching sx_dir.py exactly), use process_tgb_sxdir.py instead.

Raw TGB downloads cached in: data/raw_tgb/
(py-tgb internally prepends its package dir to root=, resolved via symlink)
"""

import argparse
import os
import sys
import numpy as np

# ---------------------------------------------------------------------------
# Available TGB link-prediction datasets (name -> approximate edge count)
# ---------------------------------------------------------------------------
AVAILABLE_DATASETS = {
    # --- Small ---
    "tgbl-uci":        59_835,
    "tgbl-enron":      125_235,
    "tgbl-wiki":       157_474,
    # --- Medium ---
    "tgbl-subreddit":  636_000,
    "tgbl-lastfm":     1_293_103,
    "tgbl-review":     4_873_540,
    # --- Large ---
    "tgbl-coin":       22_809_486,
    "tgbl-comment":    44_314_507,
    "tgbl-flight":     67_169_570,
}

DEFAULT_DATASETS = ["tgbl-wiki", "tgbl-review", "tgbl-coin"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def remap_nodes(sources: np.ndarray, destinations: np.ndarray):
    """Remap arbitrary node IDs to contiguous 0-based int32 range."""
    unique_nodes = np.unique(np.concatenate([sources, destinations]))
    mapping = {old: new for new, old in enumerate(unique_nodes)}
    remap = np.vectorize(mapping.get)
    return remap(sources).astype(np.int32), remap(destinations).astype(np.int32), len(unique_nodes)


def write_edge_list(filepath: str, sources: np.ndarray, destinations: np.ndarray):
    """Write directed edge list: one line per edge (u v).
    The -s flag in bfs will handle symmetrization (adding v u)."""
    with open(filepath, "w") as f:
        for u, v in zip(sources, destinations):
            f.write(f"{u} {v}\n")


def dedup_edges(sources: np.ndarray, destinations: np.ndarray,
                timestamps: np.ndarray):
    """
    Deduplicate edges using sx_dir.py logic:
      1. Sort by (src, dst, timestamp)
      2. Keep only the first occurrence of each (src, dst) pair
      3. Re-sort by timestamp
    """
    n = len(sources)

    # Step 1: sort by (src, dst, timestamp)
    order = np.lexsort((timestamps, destinations, sources))
    src_s = sources[order]
    dst_s = destinations[order]
    ts_s = timestamps[order]

    # Step 2: keep first occurrence of each (src, dst)
    if n == 0:
        return sources, destinations, timestamps
    # An edge is duplicate if (src, dst) == previous (src, dst)
    diff = np.ones(n, dtype=bool)
    diff[1:] = (src_s[1:] != src_s[:-1]) | (dst_s[1:] != dst_s[:-1])
    src_u = src_s[diff]
    dst_u = dst_s[diff]
    ts_u = ts_s[diff]

    # Step 3: re-sort by timestamp
    order2 = np.argsort(ts_u, kind="stable")
    return src_u[order2], dst_u[order2], ts_u[order2]


def convert_dataset(name: str, raw_dir: str, out_dir: str, base_frac: float):
    """Download one TGB dataset and convert to .base.el + .dynamic.el

    Cleaning pipeline (same logic as sx_dir.py):
      1. Remove self-loops (src == dst)
      2. Deduplicate: for each directed (u, v) pair, keep only the
         first occurrence (earliest timestamp)
      3. Re-sort by timestamp
      4. Remap node IDs to contiguous 0-based int32
      5. Split into base (first 10%) and dynamic (remaining 90%)
      6. Write as undirected edge list (both u→v and v→u per edge)
    """
    from tgb.linkproppred.dataset import LinkPropPredDataset

    print(f"\n{'='*60}")
    print(f"  Dataset: {name}")
    print(f"{'='*60}")

    # Download / load
    print(f"  Loading (raw cache: {raw_dir}) ...")
    dataset = LinkPropPredDataset(name=name, root=raw_dir, preprocess=True)
    data = dataset.full_data

    sources = data["sources"]
    destinations = data["destinations"]
    timestamps = data["timestamps"]
    n_raw = len(sources)

    print(f"  Raw edges: {n_raw:,}")
    print(f"  Time range: {timestamps.min():.0f} — {timestamps.max():.0f}")

    # Step 1: Remove self-loops
    mask = sources != destinations
    n_selfloops = (~mask).sum()
    if n_selfloops > 0:
        sources = sources[mask]
        destinations = destinations[mask]
        timestamps = timestamps[mask]
        print(f"  Removed {n_selfloops:,} self-loops")

    # Step 2 + 3: Dedup (sx_dir.py logic) — keep first (u,v), re-sort by time
    n_before_dedup = len(sources)
    sources, destinations, timestamps = dedup_edges(sources, destinations, timestamps)
    n_after_dedup = len(sources)
    n_dupes = n_before_dedup - n_after_dedup
    dup_pct = n_dupes / n_before_dedup * 100 if n_before_dedup > 0 else 0
    print(f"  Dedup: {n_before_dedup:,} → {n_after_dedup:,} "
          f"(removed {n_dupes:,} duplicates, {dup_pct:.1f}%)")

    # Step 4: Remap to contiguous int32 IDs
    sources, destinations, n_nodes = remap_nodes(sources, destinations)
    n_edges = len(sources)
    print(f"  After remap: {n_nodes:,} nodes, {n_edges:,} unique edges")

    # Step 5: Split by time — first base_frac% → base, rest → dynamic
    split_idx = int(n_edges * base_frac)
    base_src, base_dst = sources[:split_idx], destinations[:split_idx]
    dyn_src, dyn_dst = sources[split_idx:], destinations[split_idx:]

    # Ensure base covers ALL node IDs so DGAP allocates enough vertices.
    # Nodes that only appear in dynamic would cause out-of-bounds segfault.
    base_nodes = set(base_src.tolist()) | set(base_dst.tolist())
    all_nodes = set(range(n_nodes))
    missing = sorted(all_nodes - base_nodes)
    if missing:
        # Add dummy edges (missing_node, 0) to base so DGAP sees them
        pad_src = np.array(missing, dtype=np.int32)
        pad_dst = np.zeros(len(missing), dtype=np.int32)
        base_src = np.concatenate([base_src, pad_src])
        base_dst = np.concatenate([base_dst, pad_dst])
        print(f"  Padded {len(missing):,} missing nodes into base")

    print(f"  Split: base={split_idx:,} + {len(missing):,} pad ({base_frac*100:.0f}%), "
          f"dynamic={n_edges - split_idx:,} ({(1-base_frac)*100:.0f}%)")

    # Step 6: Write undirected edge list (u v and v u per edge)
    base_path = os.path.join(out_dir, f"{name}.base.el")
    dyn_path = os.path.join(out_dir, f"{name}.dynamic.el")

    print(f"  Writing {base_path} ...")
    write_edge_list(base_path, base_src, base_dst)

    print(f"  Writing {dyn_path} ...")
    write_edge_list(dyn_path, dyn_src, dyn_dst)

    # Summary
    base_lines = len(base_src)
    dyn_lines = len(dyn_src)
    base_mb = os.path.getsize(base_path) / 1024 / 1024
    dyn_mb = os.path.getsize(dyn_path) / 1024 / 1024

    print(f"  Output: base={base_lines:,} lines ({base_mb:.1f}MB), "
          f"dynamic={dyn_lines:,} lines ({dyn_mb:.1f}MB)")
    print(f"  Done: {name}")

    return {
        "name": name,
        "nodes": n_nodes,
        "edges": n_edges,
        "base_lines": base_lines,
        "dyn_lines": dyn_lines,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Download TGB datasets → DGAP edge-list format")
    parser.add_argument("--datasets", nargs="+", default=None,
                        help=f"Datasets to download (default: {DEFAULT_DATASETS})")
    parser.add_argument("--base-frac", type=float, default=0.1,
                        help="Fraction of edges for base graph (default: 0.1)")
    parser.add_argument("--raw-dir", default=None,
                        help="Directory for raw TGB cache (default: data/raw_tgb)")
    parser.add_argument("--out-dir", default=None,
                        help="Output directory for .el files (default: data/)")
    parser.add_argument("--list", action="store_true",
                        help="List available datasets and exit")
    args = parser.parse_args()

    # Resolve paths relative to repo root
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_dir = args.raw_dir or os.path.join(repo_root, "data", "raw_tgb")
    out_dir = args.out_dir or os.path.join(repo_root, "data")

    if args.list:
        print("Available TGB link-prediction datasets:")
        for name, count in AVAILABLE_DATASETS.items():
            print(f"  {name:20s}  ~{count:>12,} edges")
        sys.exit(0)

    datasets = args.datasets or DEFAULT_DATASETS

    # Validate
    for ds in datasets:
        if ds not in AVAILABLE_DATASETS:
            print(f"ERROR: Unknown dataset '{ds}'")
            print(f"Available: {list(AVAILABLE_DATASETS.keys())}")
            sys.exit(1)

    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    print(f"Raw cache:  {raw_dir}")
    print(f"Output dir: {out_dir}")
    print(f"Base frac:  {args.base_frac}")
    print(f"Datasets:   {datasets}")

    results = []
    failed = []
    for ds in datasets:
        try:
            info = convert_dataset(ds, raw_dir, out_dir, args.base_frac)
            results.append(info)
        except Exception as e:
            print(f"  ERROR: {e}")
            failed.append(ds)

    # Final summary
    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  {'Dataset':20s}  {'Nodes':>10s}  {'Edges':>12s}  {'Base lines':>12s}  {'Dyn lines':>12s}")
    print(f"  {'-'*20}  {'-'*10}  {'-'*12}  {'-'*12}  {'-'*12}")
    for r in results:
        print(f"  {r['name']:20s}  {r['nodes']:>10,}  {r['edges']:>12,}  "
              f"{r['base_lines']:>12,}  {r['dyn_lines']:>12,}")
    if failed:
        print(f"\n  FAILED: {failed}")
    print(f"\nAll done. Files ready in: {out_dir}")


if __name__ == "__main__":
    main()
