#!/usr/bin/env python3
"""Analyze TGB datasets: unique edges, unique timestamps, duplication ratios."""

import os
import numpy as np
from tgb.linkproppred.dataset import LinkPropPredDataset

datasets = [
    "tgbl-uci", "tgbl-enron", "tgbl-wiki", "tgbl-subreddit",
    "tgbl-lastfm", "tgbl-review", "tgbl-coin", "tgbl-comment", "tgbl-flight",
]
raw_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw_tgb")

print(f"{'Dataset':18s} {'Total':>12s} {'UniqueEdge':>12s} {'Dup%':>6s} {'UniqueTS':>11s} {'TS/Total':>9s}")
print("-" * 73)

for name in datasets:
    ds = LinkPropPredDataset(name=name, root=raw_dir, preprocess=True)
    data = ds.full_data
    src = data["sources"]
    dst = data["destinations"]
    ts = data["timestamps"]

    total = len(src)

    # unique (src, dst) pairs
    edge_pairs = np.stack([src, dst], axis=1)
    unique_edges = len(np.unique(edge_pairs, axis=0))
    dup_pct = (1 - unique_edges / total) * 100

    # unique timestamps
    unique_ts = len(np.unique(ts))
    ts_ratio = unique_ts / total

    print(f"{name:18s} {total:>12,} {unique_edges:>12,} {dup_pct:>5.1f}% {unique_ts:>11,} {ts_ratio:>8.4f}")
