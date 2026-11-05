#!/usr/bin/env python3
"""Stage 1 evaluation: the scenario from the project description, end to end.

    pod A  hit rate 70%  -> cost 700   (kept)
    pod B  hit rate 55%  -> cost 550   (kept)
    pod C  hit rate  5%  -> cost  50   <- removed on scale-down

Starts three fake vLLM pods on localhost, runs the real controller loop
against them twice (the first cycle only learns the counters; the second
produces a windowed hit rate), and checks the costs come out in that order.
Exit code 0 = pass.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cacheaware.controller import COST_ANNOTATION, Controller, Settings, StaticBackend  # noqa: E402
from cacheaware.scoring import Weights  # noqa: E402

PODS = {"pod-a": (9001, 0.70), "pod-b": (9002, 0.55), "pod-c": (9003, 0.05)}


def start_fake_pods():
    procs = []
    for name, (port, hit_rate) in PODS.items():
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "cacheaware.fake_vllm", "--port", str(port),
             "--hit-rate", str(hit_rate), "--usage", "0.5", "--tokens-per-sec", "5000"],
            cwd=ROOT, stdout=subprocess.DEVNULL))
    time.sleep(1.0)
    return procs


def main() -> int:
    procs = start_fake_pods()
    try:
        targets = ",".join(f"{n}=http://127.0.0.1:{p}" for n, (p, _) in PODS.items())
        # Only the hit rate counts here so the costs match the example exactly.
        settings = Settings(weights=Weights(hit_rate=1.0, kv_usage=0.0), min_delta=0)
        controller = Controller(settings, StaticBackend(targets))

        controller.run_once()          # learn the counters
        time.sleep(1.0)                # let the fake pods "serve" some traffic
        reports = controller.run_once()

        costs = {r.name: r.cost for r in reports}
        print(f"{'pod':<8}{'hit rate':>10}{'cost':>8}   verdict")
        lowest = min(costs, key=costs.get)
        for r in reports:
            verdict = "<- removed on scale-down" if r.name == lowest else "(kept)"
            print(f"{r.name:<8}{r.hit_rate:>10.2f}{r.cost:>8}   {verdict}")

        expected = {"pod-a": 700, "pod-b": 550, "pod-c": 50}
        tolerance = 15   # the fake pods' counters move a little between scrapes
        problems = [f"{n}: got {costs[n]}, expected ~{expected[n]}"
                    for n in expected if abs(costs[n] - expected[n]) > tolerance]
        if lowest != "pod-c":
            problems.append(f"lowest-cost pod is {lowest}, expected pod-c")
        if problems:
            print("\nFAIL\n  " + "\n  ".join(problems))
            return 1
        annotated = {n: int(controller.backend.annotations[n][COST_ANNOTATION]) for n in costs}
        print(f"\nannotations the controller would write: {annotated}")
        print("\nPASS: coldest cache gets the lowest pod-deletion-cost")
        return 0
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
