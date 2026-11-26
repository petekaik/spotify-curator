"""Offline evaluation harness for the artist ranker.

Runs the real `rank_artists` on a frozen snapshot of real Last.fm data and a real taste profile,
so results are repeatable and need no API keys. It answers two questions:

1. Does the ranker keep its promise? Small artists that fit the taste, not big names or artists
   the listener already knows. Metrics are compared with a committed baseline.
2. Do the tuning knobs an agent can turn over MCP do what they claim? Each check changes one
   setting and asserts the metric moves the right way (or stays put).

    python -m evals.harness                    # run, write evals/REPORT.md, exit 1 on failure
    python -m evals.harness --update-baseline  # accept current metrics as the new baseline
    python -m evals.harness --freeze           # re-snapshot data from $SPOTIFY_CURATOR_HOME
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from src.analyzer.types import UserProfile
from src.discovery.ranking import Candidate, TuningConfig, rank_artists

HERE = Path(__file__).parent
POOL = HERE / "data" / "pool.json"
PROFILE = HERE / "data" / "profile.json"
BASELINE = HERE / "baseline.json"
REPORT = HERE / "REPORT.md"

K = 15  # artists per playlist: `playlist create` ranks 30 and keeps about half after Spotify matching
TASTE_TOP_GENRES = 25
GEO_TAG = "finnish"  # geo_share measures this tag; the profile itself is strongly Finnish  # "fits the taste" = shares a tag with the profile's top 25 genres
SLACK = 1 / K  # a gated share may slip by one artist before the run fails
# Gated metrics and the direction that counts as better.
GATED = {
    "mainstream_share": "lower",
    "known_share": "lower",
    "taste_fit": "higher",
    "obscurity_lift": "higher",
}


# ── data ────────────────────────────────────────────────────────


def freeze() -> None:
    """Snapshot cached Last.fm artist info and the saved profile into evals/data/."""
    from src.analyzer.cache import load_profile
    from src.config import DATA_DIR
    from src.discovery.sources.lastfm import LastfmClient

    pool = []
    for f in sorted((DATA_DIR / "lastfm_cache").glob("artist.getInfo_*.json")):
        raw = json.loads(f.read_text()).get("artist")
        if not raw:
            continue
        a = LastfmClient._parse_artist(None, raw)  # self is unused
        pool.append({"name": a.name, "listeners": a.listeners, "tags": a.tags})
    pool.sort(key=lambda a: a["name"].casefold())

    p = load_profile()
    if p is None:
        sys.exit("no saved profile: run `spotify-curator profile build` first")
    known = sorted({a.name for tr in p.time_ranges.values() for a in tr.artists}, key=str.casefold)
    POOL.parent.mkdir(exist_ok=True)
    POOL.write_text(json.dumps(pool, indent=1, ensure_ascii=False) + "\n")
    # Genre weights and artist names only: no user id, track ids or saved library.
    PROFILE.write_text(
        json.dumps(
            {"genre_weights": p.genre_weights, "known_artists": known}, indent=1, ensure_ascii=False
        )
        + "\n"
    )
    print(f"froze {len(pool)} artists and a profile of {len(p.genre_weights)} genres")


def load() -> tuple[list[Candidate], UserProfile, set[str]]:
    pool = json.loads(POOL.read_text())
    prof = json.loads(PROFILE.read_text())
    candidates = [
        Candidate(name=a["name"], lastfm_listeners=a["listeners"], lastfm_tags=a["tags"])
        for a in pool
    ]
    profile = UserProfile(
        user_id="eval", updated_at=datetime(2026, 1, 1), genre_weights=prof["genre_weights"]
    )
    return candidates, profile, {n.casefold() for n in prof["known_artists"]}


def dataset_hash() -> str:
    h = hashlib.sha256(POOL.read_bytes() + PROFILE.read_bytes())
    return h.hexdigest()[:12]


# ── metrics ─────────────────────────────────────────────────────


def evaluate(cfg: TuningConfig, data=None) -> dict:
    candidates, profile, known = data or load()
    ranked = rank_artists(candidates, profile, config=cfg, limit=K)
    top = [c for c, _, _ in ranked]
    taste = {g for g, _ in profile.top_genres(TASTE_TOP_GENRES)}
    pool_median = statistics.median(c.lastfm_listeners for c in candidates)
    return {
        "mainstream_share": sum(c.lastfm_listeners > cfg.mainstream_listener_threshold for c in top)
        / K,
        "known_share": sum(c.name.casefold() in known for c in top) / K,
        "taste_fit": sum(bool(taste & set(c.lastfm_tags)) for c in top) / K,
        "median_listeners": statistics.median(c.lastfm_listeners for c in top),
        "obscurity_lift": pool_median / max(1, statistics.median(c.lastfm_listeners for c in top)),
        "geo_share": sum(GEO_TAG in c.lastfm_tags for c in top) / K,
        "top": [
            {
                "name": c.name,
                "score": round(s, 4),
                "listeners": c.lastfm_listeners,
                "known": c.name.casefold() in known,
            }
            for c, s, _ in ranked
        ],
    }


# Metamorphic checks: (name, tuning change, metric, expected direction vs default).
# Each is a claim the README or the MCP tool descriptions make about a knob.
CHECKS = [
    ("lower threshold → no bigger names", {"mainstream_listener_threshold": 20_000},
     "median_listeners", "not_up"),
    ("no mainstream penalty → bigger names", {"mainstream_penalty": 0.0},
     "median_listeners", "not_down"),
    ("more genre weight → no worse taste fit", {"genre_match": 0.8},
     "taste_fit", "not_down"),
    ("geo tag 'finnish' → more Finnish artists", {"preferred_geo_tags": [GEO_TAG]},
     "geo_share", "up"),
]  # fmt: skip


# Checks that fail today for a known reason. They are reported, not gated; a known failure that
# starts passing fails the run, so this list cannot go stale.
KNOWN_FAILING = {
    "geo tag 'finnish' → more Finnish artists": "geo_bonus is 0.05 × matches/3, so one tag adds"
    " 0.017: too little to move anyone into the top 15",
}


def run_checks(base_cfg: TuningConfig, data) -> list[dict]:
    out = []
    default = evaluate(base_cfg, data)
    for name, change, metric, want in CHECKS:
        after = evaluate(replace(base_cfg, **change), data)
        b, a = default[metric], after[metric]
        ok = {"not_up": a <= b, "not_down": a >= b, "up": a > b}[want]
        out.append({"check": name, "metric": metric, "before": b, "after": a, "pass": ok,
                    "known": KNOWN_FAILING.get(name)})  # fmt: skip
    first = [t["name"] for t in default["top"]]
    again = [t["name"] for t in evaluate(base_cfg, load())["top"]]
    out.append(
        {"check": "same input → same ranking", "metric": "order", "before": "", "after": "",
         "pass": first == again, "known": None}
    )  # fmt: skip
    return out


def gate(metrics: dict, baseline: dict) -> list[dict]:
    rows = []
    for m, better in GATED.items():
        b, a = baseline[m], metrics[m]
        slack = SLACK if m.endswith(("share", "fit")) else 0.10 * b
        ok = a <= b + slack if better == "lower" else a >= b - slack
        rows.append({"metric": m, "baseline": b, "now": a, "better": better, "pass": ok})
    return rows


# ── report ──────────────────────────────────────────────────────


def commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def fmt(v) -> str:
    return f"{v:.2f}" if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else str(v)


def failures(gates, checks) -> list[str]:
    """Gate misses, unexpected check failures, and known failures that now pass."""
    return [r["metric"] for r in gates if not r["pass"]] + [
        c["check"] + (" (fixed: remove from KNOWN_FAILING)" if c["known"] else "")
        for c in checks
        if c["pass"] == bool(c["known"])
    ]


def mark(c) -> str:
    return "✅" if c["pass"] else f"⚠️ known: {c['known']}" if c["known"] else "❌"


def report(metrics, gates, checks, cfg) -> str:
    ok = not failures(gates, checks)
    lines = [
        "# Ranker evaluation report",
        "",
        f"**Result: {'PASS' if ok else 'FAIL'}** · commit `{commit()}` · dataset `{dataset_hash()}`"
        f" · {datetime.now(UTC):%Y-%m-%d %H:%M} UTC · top {K} artists",
        "",
        "Generated by `python -m evals.harness`. The data is a frozen snapshot of real Last.fm"
        " artist info and one real taste profile, so every run sees the same input.",
        "",
        "## Gate: compared with the accepted baseline",
        "",
        "| Metric | Baseline | Now | Better | Pass |",
        "|---|---|---|---|---|",
        *[
            f"| {r['metric']} | {fmt(r['baseline'])} | {fmt(r['now'])} | {r['better']} |"
            f" {'✅' if r['pass'] else '❌'} |"
            for r in gates
        ],
        "",
        "- **mainstream_share:** top artists above the mainstream threshold"
        f" ({cfg.mainstream_listener_threshold:,} Last.fm listeners).",
        "- **known_share:** top artists already in the listener's own top lists. The product"
        " promises new artists, so lower is better.",
        f"- **taste_fit:** top artists sharing a tag with the profile's top {TASTE_TOP_GENRES} genres.",
        "- **obscurity_lift:** median listeners of the whole pool ÷ median of the top artists.",
        "",
        "## Behaviour checks: do the tuning knobs do what they claim?",
        "",
        "| Check | Metric | Default | Changed | Pass |",
        "|---|---|---|---|---|",
        *[
            f"| {c['check']} | {c['metric']} | {fmt(c['before'])} | {fmt(c['after'])} | {mark(c)} |"
            for c in checks
        ],
        "",
        "## Top artists with default tuning",
        "",
        "| # | Artist | Score | Listeners | Already known |",
        "|---|---|---|---|---|",
        *[
            f"| {i} | {t['name']} | {t['score']:.3f} | {t['listeners']:,} |"
            f" {'yes' if t['known'] else ''} |"
            for i, t in enumerate(metrics["top"], 1)
        ],
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--freeze", action="store_true", help="re-snapshot data from DATA_DIR")
    ap.add_argument("--update-baseline", action="store_true", help="accept current metrics")
    args = ap.parse_args(argv)
    if args.freeze:
        freeze()
        return 0

    cfg = TuningConfig()  # defaults, not the local tuning.json: the eval must be reproducible
    data = load()
    metrics = evaluate(cfg, data)
    if args.update_baseline or not BASELINE.exists():
        BASELINE.write_text(
            json.dumps(
                {"dataset": dataset_hash(), "tuning": asdict(cfg)} | {m: metrics[m] for m in GATED},
                indent=2,
            )
            + "\n"
        )
        print(f"baseline written: {BASELINE}")
    baseline = json.loads(BASELINE.read_text())
    if baseline.get("dataset") != dataset_hash():
        print("dataset changed since the baseline: review REPORT.md, then --update-baseline")
    gates, checks = gate(metrics, baseline), run_checks(cfg, data)
    REPORT.write_text(report(metrics, gates, checks, cfg))
    failed = failures(gates, checks)
    print(f"{'FAIL: ' + ', '.join(failed) if failed else 'PASS'} (report: {REPORT})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
