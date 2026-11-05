"""Stage 5 — turn the benchmark trial folders into numbers and a report.

Each trial lives in  results/bench/<mode>/trial_<n>/  and contains:

    meta.json               mode, removed pod, costs at scale-in, timings
    pre_snapshot.json       cumulative pod counters just before scale-in
    post_snapshot.json      cumulative pod counters at the end
    loadgen/summary.json    (if TOOL=loadgen)   client-side TTFT per window
    inference-perf/...      (if TOOL=inference-perf) its report files

Two ways to use it:

    python -m cacheaware.report --trial results/bench/cache-aware/trial_1   # one trial -> summary.json
    python -m cacheaware.report --results results                            # all trials -> RESULTS.md + png

The inference-perf report layout is not something we control, so
``ttft_from_inference_perf`` searches its JSON files for any object that has
a key containing "time_to_first_token" and reads mean/p50/p90/p99 from it.
If the layout changed, fix that one function (and look at the raw file).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys


# ----------------------------------------------------------------------------
# Reading one trial
# ----------------------------------------------------------------------------

def read_json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def survivor_hit_rate(pre: dict, post: dict, removed: str | None) -> float | None:
    """Prefix-cache hit rate of the pods that survived, over the measure window."""
    queries = hits = 0.0
    for pod, after in (post or {}).get("pods", {}).items():
        if pod == removed:
            continue
        before = (pre or {}).get("pods", {}).get(pod)
        if not before or after.get("queries") is None or before.get("queries") is None:
            continue
        queries += after["queries"] - before["queries"]
        hits += after["hits"] - before["hits"]
    return hits / queries if queries > 0 else None


def server_ttft_mean(pre: dict, post: dict, removed: str | None) -> float | None:
    """Mean TTFT the *server* saw on survivors (cross-check for the client numbers)."""
    total = count = 0.0
    for pod, after in (post or {}).get("pods", {}).items():
        if pod == removed:
            continue
        before = (pre or {}).get("pods", {}).get(pod)
        if not before or after.get("ttft_sum") is None or before.get("ttft_sum") is None:
            continue
        total += after["ttft_sum"] - before["ttft_sum"]
        count += after["ttft_count"] - before["ttft_count"]
    return total / count if count > 0 else None


def ttft_from_loadgen(trial_dir: str) -> dict[str, dict]:
    summary = read_json(os.path.join(trial_dir, "loadgen", "summary.json"), {})
    windows = dict(summary.get("measure_windows", {}))
    if "measure" in summary.get("phases", {}):
        windows["all"] = summary["phases"]["measure"]
    return windows


def find_ttft_objects(node, found: list) -> None:
    """Recursively collect dicts whose key mentions time_to_first_token."""
    if isinstance(node, dict):
        for key, value in node.items():
            if "time_to_first_token" in key.lower() and isinstance(value, dict):
                found.append(value)
            find_ttft_objects(value, found)
    elif isinstance(node, list):
        for item in node:
            find_ttft_objects(item, found)


def normalise_ttft(obj: dict) -> dict:
    """Map inference-perf's names onto ours (mean/p50/p90/p99)."""
    out = {}
    for ours, theirs in (("ttft_mean", ("mean", "avg")), ("ttft_p50", ("p50", "median")),
                         ("ttft_p90", ("p90",)), ("ttft_p99", ("p99",))):
        for name in theirs:
            if name in obj:
                out[ours] = obj[name]
                break
    return out


def ttft_from_inference_perf(trial_dir: str) -> dict[str, dict]:
    """One entry per stage file (stage_0 = warm-up, stage_1.. = after scale-in)."""
    windows: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(trial_dir, "inference-perf", "**", "*.json"), recursive=True)):
        name = os.path.basename(path)
        data = read_json(path)
        if data is None:
            continue
        found: list = []
        find_ttft_objects(data, found)
        if not found:
            continue
        label = "summary" if "summary" in name else name.replace(".json", "")
        windows[label] = normalise_ttft(found[0])
        counts: list = []
        find_counts(data, counts)
        if counts:
            windows[label].update(counts[0])
    return windows


def find_counts(node, found: list) -> None:
    """Pull request success/failure counts if the report has them."""
    if isinstance(node, dict):
        ok = node.get("successes", {}).get("count") if isinstance(node.get("successes"), dict) else None
        bad = node.get("failures", {}).get("count") if isinstance(node.get("failures"), dict) else None
        if ok is not None or bad is not None:
            found.append({"count": (ok or 0) + (bad or 0), "errors": bad or 0})
        for value in node.values():
            find_counts(value, found)


def summarise_trial(trial_dir: str) -> dict:
    meta = read_json(os.path.join(trial_dir, "meta.json"), {})
    pre = read_json(os.path.join(trial_dir, "pre_snapshot.json"), {})
    post = read_json(os.path.join(trial_dir, "post_snapshot.json"), {})
    removed = meta.get("removed_pod")
    ttft = ttft_from_loadgen(trial_dir) or ttft_from_inference_perf(trial_dir)
    return {
        "mode": meta.get("mode"), "trial": meta.get("trial"), "tool": meta.get("tool"),
        "removed_pod": removed, "removed_rank": meta.get("removed_rank"),
        "expected_pod": meta.get("expected_pod"),
        "costs_at_scale_in": meta.get("costs_at_scale_in", {}),
        "survivor_hit_rate": survivor_hit_rate(pre, post, removed),
        "server_ttft_mean": server_ttft_mean(pre, post, removed),
        "ttft": ttft,
    }


# ----------------------------------------------------------------------------
# Aggregating across trials
# ----------------------------------------------------------------------------

def mean_sd(values: list[float]) -> tuple[float | None, float | None]:
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    return statistics.fmean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def aggregate(trials: list[dict]) -> dict:
    """Per mode: mean/sd of the headline numbers, and how often the coldest pod was removed."""
    by_mode: dict[str, list[dict]] = {}
    for t in trials:
        by_mode.setdefault(t["mode"] or "?", []).append(t)
    out = {}
    for mode, ts in sorted(by_mode.items()):
        first = [first_window(t["ttft"]) for t in ts]
        whole = [t["ttft"].get("all") or t["ttft"].get("summary") or {} for t in ts]
        out[mode] = {
            "trials": len(ts),
            "coldest_removed": sum(1 for t in ts if t.get("removed_rank") == 1),
            "hit_rate": mean_sd([t["survivor_hit_rate"] for t in ts]),
            "server_ttft": mean_sd([t["server_ttft_mean"] for t in ts]),
            "first_p50": mean_sd([w.get("ttft_p50") for w in first]),
            "first_p90": mean_sd([w.get("ttft_p90") for w in first]),
            "first_p99": mean_sd([w.get("ttft_p99") for w in first]),
            "all_p50": mean_sd([w.get("ttft_p50") for w in whole]),
            "all_p90": mean_sd([w.get("ttft_p90") for w in whole]),
            "errors": sum((w.get("errors") or 0) for w in whole),
        }
    return out


def first_window(ttft: dict) -> dict:
    """The first measurement window after scale-in, whatever the tool called it."""
    for key in ("0-30s", "stage_1_lifecycle_metrics", "stage_1"):
        if key in ttft:
            return ttft[key]
    for key, value in ttft.items():
        if key not in ("all", "summary", "stage_0_lifecycle_metrics", "stage_0"):
            return value
    return {}


def pct_change(base: float | None, new: float | None) -> str:
    if not base or new is None:
        return "n/a"
    return f"{(new - base) / base * 100:+.1f}%"


def ms(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 1000:.0f} ms"


def write_results_md(results: str, trials: list[dict], agg: dict) -> str:
    base, cache = agg.get("baseline", {}), agg.get("cache-aware", {})
    lines = ["# Benchmark results: default vs cache-aware scale-in", "",
             "_Generated by `python -m cacheaware.report`. Numbers are means over trials "
             "(± standard deviation). TTFT = time to first token measured by the load generator; "
             "'first window' = the first 30 s after the deployment was scaled from 3 to 2 replicas._", "",
             "## Headline", "",
             "| metric | default scale-in | cache-aware scale-in | change |",
             "|---|--:|--:|--:|"]
    for label, key in (("TTFT p50, first 30 s after scale-in", "first_p50"),
                       ("TTFT p90, first 30 s after scale-in", "first_p90"),
                       ("TTFT p99, first 30 s after scale-in", "first_p99"),
                       ("TTFT p50, whole measure window", "all_p50"),
                       ("TTFT p90, whole measure window", "all_p90")):
        b, c = base.get(key, (None, None)), cache.get(key, (None, None))
        lines.append(f"| {label} | {ms(b[0])} ± {ms(b[1])} | {ms(c[0])} ± {ms(c[1])} | {pct_change(b[0], c[0])} |")
    b, c = base.get("hit_rate", (None, None)), cache.get("hit_rate", (None, None))
    lines.append(f"| prefix-cache hit rate on survivors | {fmt_rate(b[0])} | {fmt_rate(c[0])} | {pct_change(b[0], c[0])} |")
    b, c = base.get("server_ttft", (None, None)), cache.get("server_ttft", (None, None))
    lines.append(f"| server-side mean TTFT (vLLM histogram) | {ms(b[0])} | {ms(c[0])} | {pct_change(b[0], c[0])} |")
    lines.append(f"| coldest pod removed | {base.get('coldest_removed', 0)}/{base.get('trials', 0)} trials "
                 f"| {cache.get('coldest_removed', 0)}/{cache.get('trials', 0)} trials | |")
    lines.append(f"| request errors (all trials) | {base.get('errors', 0)} | {cache.get('errors', 0)} | |")

    lines += ["", "## Per trial", "",
              "| mode | trial | removed pod | cost rank (1 = lowest) | costs at scale-in | survivor hit rate | TTFT p50 first | TTFT p90 first | errors |",
              "|---|--:|---|--:|---|--:|--:|--:|--:|"]
    for t in trials:
        w = first_window(t["ttft"])
        costs = ", ".join(f"{k[-12:]}={v}" for k, v in sorted(t["costs_at_scale_in"].items()))
        lines.append(f"| {t['mode']} | {t['trial']} | {t['removed_pod']} | {t.get('removed_rank') or '-'} | {costs} "
                     f"| {fmt_rate(t['survivor_hit_rate'])} | {ms(w.get('ttft_p50'))} | {ms(w.get('ttft_p90'))} | {w.get('errors', '-')} |")

    lines += ["", "## Recovery after scale-in (TTFT p50 per 30 s window, mean over trials)", "",
              "| window | default | cache-aware |", "|---|--:|--:|"]
    for window in window_labels(trials):
        b = mean_sd([t["ttft"].get(window, {}).get("ttft_p50") for t in trials if t["mode"] == "baseline"])[0]
        c = mean_sd([t["ttft"].get(window, {}).get("ttft_p50") for t in trials if t["mode"] == "cache-aware"])[0]
        lines.append(f"| {window} | {ms(b)} | {ms(c)} |")

    lines += ["", "## Numbers for the resume bullet", "",
              f"- TTFT p90 in the first 30 s after scale-in: {ms(base.get('first_p90', (None,))[0])} -> "
              f"{ms(cache.get('first_p90', (None,))[0])} ({pct_change(base.get('first_p90', (None,))[0], cache.get('first_p90', (None,))[0])})",
              f"- Prefix-cache hit rate on the surviving replicas: {fmt_rate(base.get('hit_rate', (None,))[0])} -> "
              f"{fmt_rate(cache.get('hit_rate', (None,))[0])}",
              f"- Coldest replica removed: {cache.get('coldest_removed', 0)}/{cache.get('trials', 0)} cache-aware trials "
              f"vs {base.get('coldest_removed', 0)}/{base.get('trials', 0)} default trials", ""]
    path = os.path.join(results, "RESULTS.md")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path


def window_labels(trials: list[dict]) -> list[str]:
    labels: list[str] = []
    for t in trials:
        for key in t["ttft"]:
            if key not in ("all", "summary") and key not in labels:
                labels.append(key)
    return labels


def fmt_rate(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def plot(results: str, trials: list[dict]) -> str | None:
    """TTFT p50 per window, one line per mode. Skipped if matplotlib is missing."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    labels = window_labels(trials)
    if not labels:
        return None
    fig, ax = plt.subplots(figsize=(7, 3.6))
    for mode, colour in (("baseline", "#ea580c"), ("cache-aware", "#2563eb")):
        ys = [mean_sd([t["ttft"].get(w, {}).get("ttft_p50") for t in trials if t["mode"] == mode])[0] for w in labels]
        ax.plot(labels, [None if y is None else y * 1000 for y in ys], marker="o", color=colour, label=mode)
    ax.set_ylabel("TTFT p50 (ms)")
    ax.set_xlabel("window after scaling 3 -> 2 replicas")
    ax.set_title("Time to first token after scale-in")
    ax.legend(frameon=False)
    ax.grid(alpha=.3)
    fig.tight_layout()
    path = os.path.join(results, "ttft_after_scale_in.png")
    fig.savefig(path, dpi=150)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="benchmark report")
    parser.add_argument("--results", default=os.environ.get("RESULTS_DIR", "results"))
    parser.add_argument("--trial", help="summarise one trial folder and write its summary.json")
    args = parser.parse_args(argv)

    if args.trial:
        summary = summarise_trial(args.trial)
        with open(os.path.join(args.trial, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(summary, indent=2))
        return 0

    trial_dirs = sorted(glob.glob(os.path.join(args.results, "bench", "*", "trial_*")))
    trials = [summarise_trial(d) for d in trial_dirs]
    if not trials:
        print(f"no trials under {args.results}/bench", file=sys.stderr)
        return 1
    agg = aggregate(trials)
    path = write_results_md(args.results, trials, agg)
    png = plot(args.results, trials)
    print(open(path).read())
    print(f"\nwrote {path}" + (f" and {png}" if png else " (matplotlib not installed: no chart)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
