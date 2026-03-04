#!/usr/bin/env python3
"""
process_tgb_sxdir.py — Process TGB datasets using sx_dir.py's EXACT logic.

Difference from download_tgb.py:
  - download_tgb.py: removes self-loops THEN dedup (numpy vectorized)
  - This script: runs sx_dir.py's exact Python code (no self-loop removal)

Pipeline:
  1. Load raw TGB data (download if needed)
  2. Run sx_dir.py's exact dedup algorithm (sort by u,v,w → skip duplicates → re-sort by w)
  3. Remap node IDs to contiguous 0-based int32
  4. Split into base (10%) and dynamic (90%)
  5. Pad missing nodes into base
  6. Write as edge list (u v per line)

Output:  data/tgbl-<name>-sx.base.el  /  data/tgbl-<name>-sx.dynamic.el
"""

import os
import sys
import numpy as np

# ---- sx_dir.py's exact Edge class and dedup logic ----

class Edge:
    def __init__(self, u, v, w):
        self.u = u
        self.v = v
        self.w = w

def sx_dir_dedup(sources, destinations, timestamps):
    """
    sx_dir.py's EXACT algorithm — no self-loop removal, pure Python list+sort.
    """
    edges = []
    for u, v, w in zip(sources, destinations, timestamps):
        edges.append(Edge(int(u), int(v), int(w)))

    # Step 1: sort by (u, v, w)
    edges.sort(key=lambda x: (x.u, x.v, x.w))

    # Step 2: skip consecutive duplicates of (u, v)
    unique_edges = []
    last_u = -1
    last_v = -1
    for e in edges:
        if e.u == last_u and e.v == last_v:
            continue
        else:
            unique_edges.append(e)
            last_u = e.u
            last_v = e.v

    # Step 3: re-sort by timestamp
    unique_edges.sort(key=lambda x: x.w)

    src_out = np.array([e.u for e in unique_edges], dtype=np.int64)
    dst_out = np.array([e.v for e in unique_edges], dtype=np.int64)
    return src_out, dst_out


def remap_nodes(sources, destinations):
    unique_nodes = np.unique(np.concatenate([sources, destinations]))
    mapping = {int(old): new for new, old in enumerate(unique_nodes)}
    remap = np.vectorize(mapping.get)
    return remap(sources).astype(np.int32), remap(destinations).astype(np.int32), len(unique_nodes)


def write_edge_list(filepath, sources, destinations):
    with open(filepath, "w") as f:
        for u, v in zip(sources, destinations):
            f.write(f"{u} {v}\n")


def convert_dataset(name, raw_dir, out_dir, base_frac=0.1):
    from tgb.linkproppred.dataset import LinkPropPredDataset

    print(f"\n{'='*60}")
    print(f"  Dataset: {name}  (sx_dir.py processing)")
    print(f"{'='*60}")

    print(f"  Loading (raw cache: {raw_dir}) ...")
    dataset = LinkPropPredDataset(name=name, root=raw_dir, preprocess=True)
    data = dataset.full_data

    sources = data["sources"]
    destinations = data["destinations"]
    timestamps = data["timestamps"]
    n_raw = len(sources)
    print(f"  Raw edges: {n_raw:,}")

    # Count self-loops (for comparison, but DON'T remove them)
    n_selfloops = int((sources == destinations).sum())
    print(f"  Self-loops present: {n_selfloops:,} (NOT removed — sx_dir.py doesn't)")

    # sx_dir.py's exact dedup
    print(f"  Running sx_dir.py dedup (Python list+sort) ...")
    sources, destinations = sx_dir_dedup(sources, destinations, timestamps)
    n_after = len(sources)
    print(f"  After dedup: {n_raw:,} → {n_after:,} (removed {n_raw - n_after:,})")

    # Remap
    sources, destinations, n_nodes = remap_nodes(sources, destinations)
    n_edges = len(sources)
    print(f"  After remap: {n_nodes:,} nodes, {n_edges:,} edges")

    # Split
    split_idx = int(n_edges * base_frac)
    base_src, base_dst = sources[:split_idx], destinations[:split_idx]
    dyn_src, dyn_dst = sources[split_idx:], destinations[split_idx:]

    # Pad missing nodes
    base_nodes = set(base_src.tolist()) | set(base_dst.tolist())
    all_nodes = set(range(n_nodes))
    missing = sorted(all_nodes - base_nodes)
    if missing:
        pad_src = np.array(missing, dtype=np.int32)
        pad_dst = np.zeros(len(missing), dtype=np.int32)
        base_src = np.concatenate([base_src, pad_src])
        base_dst = np.concatenate([base_dst, pad_dst])
        print(f"  Padded {len(missing):,} missing nodes into base")

    print(f"  Split: base={split_idx:,} + {len(missing):,} pad, dynamic={n_edges - split_idx:,}")

    # Write
    base_path = os.path.join(out_dir, f"{name}-sx.base.el")
    dyn_path = os.path.join(out_dir, f"{name}-sx.dynamic.el")

    print(f"  Writing {base_path} ...")
    write_edge_list(base_path, base_src, base_dst)
    print(f"  Writing {dyn_path} ...")
    write_edge_list(dyn_path, dyn_src, dyn_dst)

    base_mb = os.path.getsize(base_path) / 1024 / 1024
    dyn_mb = os.path.getsize(dyn_path) / 1024 / 1024
    print(f"  Output: base={len(base_src):,} lines ({base_mb:.1f}MB), "
          f"dynamic={len(dyn_src):,} lines ({dyn_mb:.1f}MB)")
    print(f"  Done: {name}")

    return {"name": name, "nodes": n_nodes, "edges": n_edges}


DEFAULT_DATASETS = ["tgbl-review", "tgbl-coin", "tgbl-comment", "tgbl-flight"]


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Process TGB datasets using sx_dir.py logic (no self-loop removal)")
    parser.add_argument("--datasets", nargs="+", default=None,
                        help=f"Datasets to process (default: {DEFAULT_DATASETS})")
    parser.add_argument("--base-frac", type=float, default=0.1,
                        help="Fraction of edges for base graph (default: 0.1)")
    parser.add_argument("--raw-dir", default=None,
                        help="Directory for raw TGB cache (default: data/raw_tgb)")
    parser.add_argument("--out-dir", default=None,
                        help="Output directory for .el files (default: data/)")
    args = parser.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_dir = args.raw_dir or os.path.join(repo_root, "data", "raw_tgb")
    out_dir = args.out_dir or os.path.join(repo_root, "data")
    datasets = args.datasets or DEFAULT_DATASETS

    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    print(f"Raw cache:  {raw_dir}")
    print(f"Output dir: {out_dir}")
    print(f"Datasets:   {datasets}")
    print(f"Using sx_dir.py's exact dedup logic (no self-loop removal)")

    for ds in datasets:
        try:
            convert_dataset(ds, raw_dir, out_dir, base_frac=args.base_frac)
        except Exception as e:
            print(f"  ERROR processing {ds}: {e}")
            import traceback
            traceback.print_exc()

    print("\nDone. Files saved as *-sx.base.el / *-sx.dynamic.el")


if __name__ == "__main__":
    main()
