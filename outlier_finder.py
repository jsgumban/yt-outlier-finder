#!/usr/bin/env python3
"""YouTube outlier finder.

Searches YouTube for niche keywords, collects the channels that show up,
pulls each channel's recent long-form uploads, and flags videos that got
far more views than that channel's median. Small channels with big
outliers = proven demand you can compete for.

No API key needed: uses yt-dlp under the hood.

Example:
    python3 outlier_finder.py "plane crash explained" "aviation disaster" \
        --max-subs 100000 --min-ratio 5
"""

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".cache"
REPORTS_DIR = ROOT / "reports"
CACHE_TTL = 24 * 3600


def ytdlp_json(url, extra=()):
    cmd = ["yt-dlp", "--flat-playlist", "-J", "--no-warnings", *extra, url]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return json.loads(out.stdout)


def cached(key, fetch):
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL:
        return json.loads(path.read_text())
    data = fetch()
    if data is not None:
        path.write_text(json.dumps(data))
    return data


def search_channels(query, n):
    data = ytdlp_json(f"ytsearch{n}:{query}")
    channels = {}
    for e in (data or {}).get("entries", []):
        if e.get("channel_id"):
            channels[e["channel_id"]] = e.get("channel") or e["channel_id"]
    return channels


def fetch_channel(channel_id, recent):
    def fetch():
        d = ytdlp_json(
            f"https://www.youtube.com/channel/{channel_id}/videos",
            ["--playlist-end", str(recent)],
        )
        if not d:
            return None
        return {
            "id": channel_id,
            "name": d.get("channel") or d.get("uploader") or channel_id,
            "subs": d.get("channel_follower_count"),
            "videos": [
                {
                    "id": e["id"],
                    "title": e.get("title"),
                    "views": e.get("view_count"),
                    "duration": e.get("duration"),
                    "position": i,  # 0 = newest
                }
                for i, e in enumerate(d.get("entries", []))
                if e.get("id")
            ],
        }

    return cached(f"ch_{channel_id}_{recent}", fetch)


def fetch_upload_date(video_id):
    def fetch():
        out = subprocess.run(
            ["yt-dlp", "--skip-download", "--no-warnings", "--print", "%(upload_date)s",
             f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=60,
        )
        date = out.stdout.strip()
        return {"upload_date": date} if date and date != "NA" else None

    d = cached(f"vid_{video_id}", fetch)
    if not d:
        return ""
    s = d["upload_date"]
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def analyze(channel, args):
    subs = channel.get("subs")
    if subs is not None and (subs > args.max_subs or subs < args.min_subs):
        return [], None
    vids = [
        v for v in channel["videos"]
        if v["views"] is not None
        and v["duration"] is not None
        and v["duration"] >= args.min_duration
    ]
    if len(vids) < args.min_videos:
        return [], None
    median = statistics.median(v["views"] for v in vids)
    if median <= 0:
        return [], None

    outliers = []
    for v in vids:
        ratio = v["views"] / median
        if ratio >= args.min_ratio and v["views"] >= args.min_views:
            outliers.append({
                "ratio": round(ratio, 1),
                "views": v["views"],
                "title": v["title"],
                "channel": channel["name"],
                "subs": subs,
                "channel_median": int(median),
                "views_per_sub": round(v["views"] / subs, 1) if subs else "",
                "duration_min": round(v["duration"] / 60, 1),
                "recency_rank": v["position"] + 1,
                "url": f"https://www.youtube.com/watch?v={v['id']}",
                "video_id": v["id"],
            })
    summary = {
        "channel": channel["name"],
        "subs": subs,
        "median_views": int(median),
        "videos_checked": len(vids),
        "outliers": len(outliers),
        "best_ratio": max((o["ratio"] for o in outliers), default=0),
        "url": f"https://www.youtube.com/channel/{channel['id']}",
    }
    return outliers, summary


def fmt(n):
    if n is None or n == "":
        return "?"
    n = float(n)
    for unit, div in (("M", 1e6), ("K", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}"
    return str(int(n))


def write_reports(queries, outliers, summaries, args):
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    slug = "_".join(q.replace(" ", "-") for q in queries)[:60]
    base = REPORTS_DIR / f"{stamp}_{slug}"

    if outliers:
        with open(f"{base}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(outliers[0].keys()))
            w.writeheader()
            w.writerows(outliers)

    lines = [
        f"# Outliers: {', '.join(queries)}",
        "",
        f"_{datetime.now():%Y-%m-%d %H:%M} · channels {fmt(args.min_subs)}–{fmt(args.max_subs)} subs · "
        f"≥{args.min_ratio}× channel median · ≥{args.min_duration // 60} min videos_",
        "",
        "## Top outlier videos",
        "",
        "| Ratio | Views | Title | Channel (subs) | Len | Uploaded |",
        "|---:|---:|---|---|---:|---|",
    ]
    for o in outliers[: args.top]:
        title = (o["title"] or "").replace("|", "/")
        date = o.get("upload_date") or f"#{o['recency_rank']} newest"
        lines.append(
            f"| {o['ratio']}× | {fmt(o['views'])} | [{title}]({o['url']}) | "
            f"{o['channel']} ({fmt(o['subs'])}) | {o['duration_min']}m | {date} |"
        )

    hot = sorted((s for s in summaries if s["outliers"]),
                 key=lambda s: (s["outliers"], s["best_ratio"]), reverse=True)
    lines += [
        "",
        "## Channels with repeat outliers (study these)",
        "",
        "| Channel | Subs | Median views | Outliers | Best |",
        "|---|---:|---:|---:|---:|",
    ]
    for s in hot[:25]:
        lines.append(
            f"| [{s['channel']}]({s['url']}) | {fmt(s['subs'])} | {fmt(s['median_views'])} | "
            f"{s['outliers']}/{s['videos_checked']} | {s['best_ratio']}× |"
        )

    lines += [
        "",
        "## Niche signal",
        "",
        f"- Channels scanned (within sub range): {len(summaries)}",
        f"- Channels with ≥1 outlier: {len(hot)}",
        f"- Outlier videos ≥100K views: {sum(1 for o in outliers if o['views'] >= 100_000)}",
        f"- Small channels (<10K subs) with a ≥100K video: "
        f"{len({o['channel'] for o in outliers if o['subs'] and o['subs'] < 10_000 and o['views'] >= 100_000})}",
    ]
    Path(f"{base}.md").write_text("\n".join(lines) + "\n")
    return base


def main():
    p = argparse.ArgumentParser(description="Find YouTube outlier videos in a niche.")
    p.add_argument("queries", nargs="+", help="search keywords (one niche, several phrasings)")
    p.add_argument("--per-query", type=int, default=40, help="search results per query (default 40)")
    p.add_argument("--recent", type=int, default=30, help="recent uploads checked per channel (default 30)")
    p.add_argument("--min-ratio", type=float, default=5, help="views ÷ channel median to count as outlier (default 5)")
    p.add_argument("--min-views", type=int, default=20_000, help="ignore outliers below this many views (default 20000)")
    p.add_argument("--min-subs", type=int, default=1_000)
    p.add_argument("--max-subs", type=int, default=250_000, help="skip big channels (default 250000)")
    p.add_argument("--min-duration", type=int, default=480, help="seconds; long-form only (default 480 = 8 min)")
    p.add_argument("--min-videos", type=int, default=6, help="need this many long videos for a fair median")
    p.add_argument("--top", type=int, default=40, help="rows in the report (default 40)")
    p.add_argument("--no-dates", action="store_true", help="skip fetching upload dates (faster)")
    p.add_argument("--workers", type=int, default=6)
    args = p.parse_args()

    channels = {}
    for q in args.queries:
        print(f"Searching: {q!r} …", file=sys.stderr)
        channels.update(search_channels(q, args.per_query))
    print(f"Found {len(channels)} channels. Scanning their uploads …", file=sys.stderr)

    outliers, summaries = [], []
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(fetch_channel, cid, args.recent): name for cid, name in channels.items()}
        for i, fut in enumerate(as_completed(futures), 1):
            ch = fut.result()
            if ch:
                o, s = analyze(ch, args)
                outliers += o
                if s:
                    summaries.append(s)
            print(f"\r  {i}/{len(futures)} channels", end="", file=sys.stderr)
    print(file=sys.stderr)

    outliers.sort(key=lambda o: o["ratio"], reverse=True)

    if outliers and not args.no_dates:
        top = outliers[: args.top]
        print(f"Fetching upload dates for top {len(top)} …", file=sys.stderr)
        with ThreadPoolExecutor(args.workers) as pool:
            for o, date in zip(top, pool.map(lambda o: fetch_upload_date(o["video_id"]), top)):
                o["upload_date"] = date
        for o in outliers[args.top:]:
            o["upload_date"] = ""

    base = write_reports(args.queries, outliers, summaries, args)
    print(f"\n{len(outliers)} outliers across {len(summaries)} channels.")
    for o in outliers[:10]:
        print(f"  {o['ratio']:>6}×  {fmt(o['views']):>6}  {o['channel'][:22]:<22}  {(o['title'] or '')[:60]}")
    print(f"\nReport: {base}.md" + (f"\nCSV:    {base}.csv" if outliers else ""))


if __name__ == "__main__":
    main()
