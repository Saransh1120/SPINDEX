"""Regenerate the handbook's own figures from the pipeline.

    python handbook/tools/make_figures.py

Writes into handbook/assets/img/. Everything here is derived from the
repository's forward model, so the figures cannot drift away from the code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

import config
import forward_model as fm
import render_frames as rf

OUT = ROOT / "handbook" / "assets" / "img"
OUT.mkdir(parents=True, exist_ok=True)

# The handbook is dark-first, so the figures are drawn on its own panel colour
# rather than on white and then inverted.
BG = "#111823"
INK = "#e9eef7"
MUTED = "#8fa0b8"
BLUE = "#5aa2ff"
AMBER = "#ff9f43"
FRAME = "#2b394d"

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "text.color": INK, "axes.labelcolor": MUTED, "xtick.color": MUTED,
    "ytick.color": MUTED, "axes.edgecolor": FRAME, "font.family": "DejaVu Sans",
})


def hero_glint():
    """Five rendered phases centred on the brightest glint, over the curve."""
    truth = config.TRUTH
    params = fm.default_params("B", truth)
    facets = fm.MODELS["B"](panel_area=params.panel_area)

    fps, span = 60.0, 7.5
    t = np.arange(int(span * fps)) / fps
    flux = fm.light_curve(t, params, facets)
    phi = 2 * np.pi * t / params.p_spin + params.spin_phase0

    # Five frames, odd count, centred exactly on the brightest glint. An even
    # count straddles the peak and never lands on it, which defeats the figure.
    peak_i = int(np.argmax(flux))
    offs = np.array([-0.40, -0.20, 0.0, 0.20, 0.40]) * params.p_spin * fps
    idx = [max(0, min(len(t) - 1, int(round(peak_i + o)))) for o in offs]
    glint_k = 2

    fig = plt.figure(figsize=(16, 5.9))
    gs = GridSpec(2, 5, figure=fig, height_ratios=[1.0, 0.92],
                  hspace=0.36, wspace=0.085,
                  left=0.052, right=0.986, top=0.855, bottom=0.135)

    W = H = 320
    for k, i in enumerate(idx):
        ax = fig.add_subplot(gs[0, k])
        pat = rf.target_pattern((H, W), W / 2, H / 2, phi[i], bus=(28, 46),
                                panel=(95, 18), blur=3.2)
        pat = pat / pat.max()
        lvl = float(flux[i] / flux.max())
        ax.imshow(pat * (0.22 + 0.78 * lvl ** 0.45), cmap="bone", vmin=0, vmax=1,
                  interpolation="bilinear")
        ax.set_xticks([]); ax.set_yticks([])
        hot = (k == glint_k)
        for sp in ax.spines.values():
            sp.set_color(BLUE if hot else FRAME)
            sp.set_linewidth(2.6 if hot else 1.0)
        ax.set_title(f"t = {t[i]:.2f} s", fontsize=12,
                     color=BLUE if hot else MUTED,
                     fontweight="bold" if hot else "normal", pad=8)

    axc = fig.add_subplot(gs[1, :])
    axc.plot(t, flux, lw=2.1, color=BLUE)
    axc.fill_between(t, 0, flux, color=BLUE, alpha=0.14)
    for k, i in enumerate(idx):
        axc.axvline(t[i], color=MUTED, lw=0.9, ls=":", alpha=0.5)
        axc.plot([t[i]], [flux[i]], "o", ms=10 if k == glint_k else 7,
                 color=BLUE if k == glint_k else INK, zorder=5)
    axc.set_xlim(t[0], t[-1]); axc.set_ylim(0, flux.max() * 1.30)
    axc.set_xlabel("time  (seconds)", fontsize=13, labelpad=7)
    axc.set_ylabel("brightness", fontsize=13, labelpad=7)
    axc.tick_params(labelsize=11)
    axc.grid(alpha=0.12, color=MUTED)
    for sp in ("top", "right"):
        axc.spines[sp].set_visible(False)

    pk = t[idx[glint_k]]
    axc.annotate("PANEL FACE-ON  =  GLINT", xy=(pk, flux.max() * 1.015),
                 xytext=(pk, flux.max() * 1.21), color=BLUE, fontsize=13.5,
                 fontweight="bold", ha="center",
                 arrowprops=dict(arrowstyle="-|>", color=BLUE, lw=2.0))
    axc.annotate("EDGE-ON  =  NEARLY DARK",
                 xy=(t[idx[0]], flux[idx[0]]),
                 xytext=(t[idx[0]] - 2.9, flux.max() * 0.42),
                 color=MUTED, fontsize=12.5, ha="left",
                 arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.5))

    fig.text(0.052, 0.945, "ONE LINE OF DATA CARRIES THE WHOLE MOTION",
             fontsize=21, fontweight="bold", color=INK)
    path = OUT / "hero_glint.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    hero_glint()
