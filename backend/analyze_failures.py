"""Analyze batch_failures.txt by genre/album"""
import os

with open(os.path.join(os.path.dirname(__file__), "batch_failures.txt"), "r", encoding="utf-8") as f:
    lines = f.readlines()

buckets = {}

for line in lines:
    line = line.strip()
    if not line.startswith("[NO_JSON]"):
        continue
    path = line.split(") ", 1)[1]
    parts = path.replace("\\", "/").split("/")
    genre = parts[0]
    album = parts[1] if len(parts) > 1 else "(root)"

    if genre not in buckets:
        buckets[genre] = {}
    buckets[genre][album] = buckets[genre].get(album, 0) + 1

for genre in sorted(buckets.keys()):
    items = buckets[genre]
    total = sum(items.values())
    print(f"\n=== {genre} ({total}) ===")
    for k, v in sorted(items.items(), key=lambda x: -x[1])[:20]:
        print(f"  {v:4d}  {k}")
    if len(items) > 20:
        print(f"  ... {len(items)} unique folders total")
