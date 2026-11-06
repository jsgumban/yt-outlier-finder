# YouTube Outlier Finder

Finds videos that got far more views than their channel normally gets (≥5× the channel's median),
on small and mid-size channels. That points to topics with proven demand that a new channel can compete for.

No API key needed: it uses `yt-dlp`.

## Usage

```bash
python3 outlier_finder.py "plane crash explained" "aviation incident breakdown"
```

Give several phrasings of the same niche to find more channels. Reports go to `reports/` as `.md` + `.csv`.

| Flag | Default | Meaning |
|---|---|---|
| `--per-query` | 40 | search results per query (more = more channels) |
| `--recent` | 30 | recent uploads checked per channel |
| `--min-ratio` | 5 | views ÷ channel median to count as an outlier |
| `--min-views` | 20000 | ignore outliers with fewer views |
| `--min-subs` / `--max-subs` | 1000 / 250000 | channel size range |
| `--min-duration` | 480 | seconds; long-form only (8 min) |
| `--no-dates` | off | skip upload-date lookups (faster) |

Channel data is cached for 24h in `.cache/`, so re-runs with different thresholds are quick.

## Reading the results

- **Repeat outliers on one channel**: study its titles and thumbnails; that channel has found a format that works.
- **"Small channels (<10K subs) with a ≥100K video"** is the key niche signal: high means a newcomer can break in.
- Ignore pirated TV re-uploads and off-topic channels that matched the search.
