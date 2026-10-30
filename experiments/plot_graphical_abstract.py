#!/usr/bin/env python3
"""Graphical abstract (Elsevier: at least 1328 x 531 px, w x h), from the frozen summary.

Left: where the last check runs, from decision to effect. Right: effects on a paused,
controller-owned line when the command is checked against XAIR's copy of the context
(unconditional) or executed only if the controller's own version is unchanged
(conditional), pooled over the optimistic and atomic paths of E21, E22, and E24.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

from common import ROOT  # noqa: E402
from plot_key_results import AQUA, BLUE, GRID, INK, MUTED, ORANGE  # noqa: E402

RED = "#c62e2e"


def effects(src: dict, key: str, cells: tuple[str, ...]) -> tuple[int, int]:
    k = n = 0
    for c in src:
        for cell in cells:
            e = c.get(key, {}).get(cell)
            if e:
                k += e["stale_effects"]["k"]; n += e["stale_effects"]["n"]
    return k, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", type=Path, default=ROOT / "data" / "execution-gap" / "paper_metrics_summary.json")
    ap.add_argument("--out", type=Path, default=ROOT / "journal" / "figures" / "graphical_abstract.png")
    a = ap.parse_args()
    d = json.loads(a.summary.read_text())
    dist = d["distributed"]
    camps = [dist] + list(dist.get("campaigns", {}).values())
    sources = [("Emulated OPC UA\ncontroller", "e21", camps, "plc"),
               ("Physical node,\nwide area", "e21", [d.get("physical", {})], "plc"),
               ("Physical node,\nWi-Fi LAN", "e21", [d.get("lan", {})], "plc"),
               ("OpenPLC\nruntime", "e24", [d.get("openplc", {})], "openplc")]
    sources = [s for s in sources if any(c.get(s[1]) for c in s[2])]

    fig = plt.figure(figsize=(13.28, 5.31), dpi=200)
    fig.patch.set_facecolor("white")
    # --- left: timeline
    ax = fig.add_axes([0.02, 0.06, 0.50, 0.86])
    ax.set_xlim(0, 10); ax.set_ylim(0, 6); ax.axis("off")
    ax.text(0.1, 5.55, "The execution gap", fontsize=17, weight="bold", color=INK)
    ax.text(0.1, 5.05, "an action admissible when decided takes effect after the plant changed",
            fontsize=11, color=MUTED)
    ax.annotate("", xy=(9.8, 3.4), xytext=(0.3, 3.4), arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.6))
    for x, lab, sub in ((0.6, "$t_d$", "decision"), (2.4, "$t_v$", "validation"), (4.6, "$t_g$ / $t_c$", "last check"),
                        (6.8, "$t_m$", "release"), (8.9, "$t_a$", "effect")):
        ax.plot([x], [3.4], "o", color=INK, ms=7)
        ax.text(x, 3.85, lab, ha="center", fontsize=13, color=INK)
        ax.text(x, 2.85, sub, ha="center", fontsize=10.5, color=MUTED)
    ax.plot([4.6, 8.9], [2.35, 2.35], color=RED, lw=6, solid_capstyle="butt")
    ax.text(6.75, 1.9, "a change after a check on a copy escapes it", ha="center", fontsize=10.5, color=RED)
    ax.add_patch(FancyBboxPatch((7.6, 0.25), 2.3, 1.15, boxstyle="round,pad=0.05", fc="#e8f7f1", ec="#1baf7a", lw=1.5))
    ax.text(8.75, 0.83, "controller checks\nits own version\nin the same scan", ha="center", va="center", fontsize=10, color="#0d6b4a")
    ax.add_patch(FancyBboxPatch((0.3, 0.25), 6.9, 1.15, boxstyle="round,pad=0.05", fc="#fdeee8", ec=ORANGE, lw=1.5))
    ax.text(3.75, 0.83, "AIS: the producer states validity and preconditions\n"
            "XAIR: rechecks versions, or commits atomically in the store", ha="center", va="center", fontsize=10, color=INK)
    # --- right: result
    bx = fig.add_axes([0.66, 0.14, 0.32, 0.62])
    y = list(range(len(sources)))[::-1]
    h = 0.36
    for yi, (lab, key, src, act) in zip(y, sources):
        ku, nu = effects(src, key, (f"xair|{act}_plain", f"xair_atomic|{act}_plain"))
        kc, nc = effects(src, key, (f"xair|{act}_conditional", f"xair_atomic|{act}_conditional"))
        bx.barh(yi + h / 2, 100 * ku / nu, height=h, color=BLUE)
        bx.barh(yi - h / 2, 100 * kc / nc, height=h, color=AQUA, hatch="\\\\", edgecolor="white")
        bx.plot([0], [yi - h / 2], marker="D", ms=8, color=AQUA, mec=INK, mew=0.6, clip_on=False) if kc == 0 else None
        bx.text(100 * ku / nu + 1.5, yi + h / 2, f"{100 * ku / nu:.0f}%", va="center", fontsize=11, color=INK)
        bx.text(100 * kc / nc + 3, yi - h / 2, f"{kc}/{nc}", va="center", fontsize=11, color=INK)
    bx.set_yticks(y, [s[0] for s in sources], fontsize=10.5, color=INK)
    bx.set_xlim(0, 70)
    bx.set_xlabel("injected trials with an effect on a paused line [%]", fontsize=10.5, color=MUTED)
    for s in ("top", "right"):
        bx.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        bx.spines[s].set_color(MUTED)
    bx.tick_params(colors=MUTED, labelsize=10)
    bx.xaxis.grid(True, color=GRID); bx.set_axisbelow(True)
    fig.text(0.66, 0.9, "Checking a copy is not enough", fontsize=15, weight="bold", color=INK)
    fig.text(0.66, 0.855, "■ command checked against XAIR's copy", fontsize=10.5, color=BLUE)
    fig.text(0.66, 0.815, "■ command conditional on the controller's own version", fontsize=10.5, color=AQUA)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(a.out, dpi=200)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
