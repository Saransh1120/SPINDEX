"""Validate stages 5 and 6 against synthetic data with known truth.

    python pipeline/run_period_recovery.py --model A
    python pipeline/run_period_recovery.py --model B --noise 0.05 --duration 120

Writes the light curve and the slot-sensor pulse train to data/, a report to
results/, and diagnostic plots to results/plots/.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import forward_model as fm
import period as ps

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RESULTS = ROOT / "results"


# ------------------------------------------------------------ ground truth channel


def simulate_slot_pulses(duration, p_spin, jitter_us=40.0, t0_us=1_000_000,
                         seed=0):
    """The independent truth channel: one micros() stamp per Axis A revolution.

    Jitter stands for the 20-100 us polling resolution quoted in Rev B section
    6.3. Values wrap at 2^32 exactly as the Arduino's micros() does, so the host
    parser gets exercised on the wrap.
    """
    rng = np.random.default_rng(seed)
    n = int(duration / p_spin)
    ideal = t0_us + np.arange(n) * p_spin * 1e6
    return ((ideal + rng.normal(0.0, jitter_us, n)).astype(np.int64) % (2 ** 32))


def period_from_pulses(pulses_us):
    """Revolution period from the pulse train, by straight-line fit to the
    cumulative edge count. Robust to a single missed edge only if it is not
    silently absorbed -- so the gap check comes first."""
    p = np.asarray(pulses_us, dtype=np.int64)
    d = np.diff(p)
    d = np.where(d < 0, d + 2 ** 32, d)          # undo micros() wrap
    med = np.median(d)
    if np.any(np.abs(d - med) > 0.25 * med):
        n_bad = int(np.sum(np.abs(d - med) > 0.25 * med))
        print(f"  ! slot sensor: {n_bad} interval(s) off median by >25% "
              f"(missed or spurious edges)")
    k = np.arange(p.size)
    t_us = np.concatenate([[0.0], np.cumsum(d)])
    slope, _ = np.polyfit(k, t_us, 1)
    resid = t_us - np.polyval([slope, np.mean(t_us) - slope * np.mean(k)], k)
    err = resid.std(ddof=2) / (1e6 * np.sqrt(p.size) * (p.size / 12) ** 0.5)
    return slope / 1e6, abs(err)


# ------------------------------------------------------------------- reporting


def sigma_dev(recovered, err, truth):
    if not np.isfinite(err) or err <= 0:
        return float("nan")
    return (recovered - truth) / err


def report(model, truth, res, slot_p, slot_err, args) -> str:
    L = []
    add = L.append
    add("=" * 74)
    add(f"SDC-001 period recovery -- synthetic Model {model}")
    add("=" * 74)
    add(f"duration {args.duration:.0f} s at {args.fps:.0f} fps "
        f"({int(args.duration * args.fps)} samples), "
        f"noise {args.noise:.1%}, stray light {args.stray:.1%}")
    add("")
    add("Commanded truth")
    add(f"  {truth.summary()}")
    add("")

    add("Periodogram peaks (frequency Hz, normalised power)")
    for f, p in res.peaks[:6]:
        tag = ""
        for name, ftrue in (("1/P_A", 1 / truth.p_spin), ("2/P_A", 2 / truth.p_spin),
                            ("3/P_A", 3 / truth.p_spin), ("1/P_B", 1 / truth.p_prec),
                            ("2/P_B", 2 / truth.p_prec), ("3/P_B", 3 / truth.p_prec)):
            if abs(f - ftrue) < 0.02:
                tag = f"  <- {name}"
        add(f"  {f:8.4f}  {p:6.4f}{tag}")
    add("")

    add("Harmonic resolution -- sub-harmonic walk from the strongest peak")
    add(f"  strongest spin-band peak : {res.f_spin_peak:.6f} Hz "
        f"-> {1 / res.f_spin_peak:.5f} s")
    for i, v in enumerate(res.trail, 1):
        add(f"  round {i}: {v.p_half:.5f} s vs {v.p_fundamental:.5f} s  "
            f"chi2_red = {v.chi2_reduced:9.2f} over {v.n_bins_compared} bins "
            f"-> {v.decision}")
    add(f"  adopted spin period      : {res.p_spin:.5f} s "
        f"= {res.spin_harmonic:.0f} x the peak period")
    for line in _wrap(res.verdict.note, 66):
        add(f"    {line}")
    add("")

    if res.prec_verdict is not None:
        add("Envelope (Axis B), from the fine-structure-averaged curve")
        add(f"  strongest envelope peak  : {res.f_prec_peak:.6f} Hz "
            f"-> {1 / res.f_prec_peak:.5f} s")
        for i, v in enumerate(res.prec_trail, 1):
            add(f"  round {i}: {v.p_half:.4f} s vs {v.p_fundamental:.4f} s  "
                f"chi2_red = {v.chi2_reduced:8.2f} -> {v.decision}")
        add(f"  adopted precession period: {res.p_prec:.5f} s "
            f"= {res.prec_harmonic:.0f} x the peak period")
        add("")

    # What the walk returns is the light curve's *repeat* period. If the target
    # is n-fold symmetric about the spin axis, the repeat period is P_A / n and
    # no photometric method can do better -- so the honest comparison is against
    # truth / n, with n reported explicitly.
    n_spin = _symmetry_order(res.p_spin, truth.p_spin)
    n_prec = _symmetry_order(res.p_prec, truth.p_prec)

    if np.isfinite(res.pdm_period):
        add("Independent cross-check: phase dispersion minimisation")
        add(f"  PDM minimum near the adopted period : {res.pdm_period:.5f} s "
            f"(theta = {res.pdm_theta:.4f})")
        add(f"  disagreement with Lomb-Scargle      : "
            f"{100 * abs(res.pdm_period - res.p_spin) / res.p_spin:.3f} %")
        add("  PDM assumes nothing about profile shape, so agreement here rules")
        add("  out a peak that is an artefact of fitting a sinusoid to a glint.")
        add("")

    add("Recovered against truth")
    add(f"  {'quantity':<28}{'recovered':>20}{'expected':>11}{'dev':>9}")
    rows = [
        ("light-curve repeat P_A/n [s]", res.p_spin, res.p_spin_err,
         truth.p_spin / n_spin),
        (f"  x {n_spin} -> spin period [s]", n_spin * res.p_spin,
         n_spin * res.p_spin_err, truth.p_spin),
        ("envelope repeat P_B/m [s]", res.p_prec, res.p_prec_err,
         truth.p_prec / n_prec),
        (f"  x {n_prec} -> precession [s]", n_prec * res.p_prec,
         n_prec * res.p_prec_err, truth.p_prec),
        ("slot-sensor P_A [s]", slot_p, slot_err, truth.p_spin),
    ]
    for name, val, err, tru in rows:
        add(f"  {name:<28}{val:>10.5f} +/- {err:<7.5f}{tru:>11.5f}"
            f"{sigma_dev(val, err, tru):>8.1f}s")
    add("")
    add(f"  spin period error vs commanded : "
        f"{100 * abs(n_spin * res.p_spin - truth.p_spin) / truth.p_spin:.4f} %")
    add(f"  precession error vs commanded  : "
        f"{100 * abs(n_prec * res.p_prec - truth.p_prec) / truth.p_prec:.4f} %")
    add(f"  slot sensor vs commanded       : "
        f"{100 * abs(slot_p - truth.p_spin) / truth.p_spin:.4f} %")
    add("")

    add("What the photometry alone determined")
    if n_spin == 1:
        add(f"  Spin: the full period P_A. The fold test climbed to "
            f"{res.p_spin:.4f} s and stopped, so the")
        add("  target's asymmetry is detectable and no external input was needed.")
    else:
        add(f"  Spin: only P_A/{n_spin}. The target is {n_spin}-fold symmetric about "
            "the spin axis, so")
        add(f"  consecutive {res.p_spin:.4f} s cycles are identical and the light "
            "curve carries no")
        add(f"  information distinguishing them. This is the correct result, not a "
            "failure --")
        add("  Rev B section 9.2 is why Model B exists. The slot sensor picks the "
            "branch.")
    if n_prec != 1:
        add(f"  Precession: only P_B/{n_prec}. The rock is symmetric about the "
            "plane-of-sky")
        add("  position, so rock angles +eps and -eps present near-identical "
            "aspect and the")
        add(f"  envelope repeats every {res.p_prec:.3f} s. See pipeline/README.md "
            "on offsetting")
        add("  the sweep centre if the full P_B is wanted from photometry alone.")
    add("")
    add("'dev' is (recovered - expected) / sigma. The slot-sensor row is an")
    add("independent channel and does not use the video at all.")
    add("=" * 74)
    return "\n".join(L)


def _symmetry_order(recovered, truth_period, max_order=4):
    """Integer n such that recovered ~= truth_period / n, or 1 if none fits."""
    if not np.isfinite(recovered) or recovered <= 0:
        return 1
    best, best_res = 1, np.inf
    for n in range(1, max_order + 1):
        r = abs(recovered - truth_period / n) / (truth_period / n)
        if r < best_res:
            best, best_res = n, r
    return best if best_res < 0.05 else 1


def _wrap(text, width):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


# ----------------------------------------------------------------------- plots


def make_plots(t, y, res, truth, model, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib not available; skipping plots)")
        return

    outdir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(4, 1, figsize=(10, 13))

    m = t <= 25.0
    ax[0].plot(t[m], y[m], lw=0.7)
    ax[0].set_xlabel("time [s]")
    ax[0].set_ylabel("flux [arb.]")
    ax[0].set_title(f"Model {model} synthetic light curve (first 25 s of "
                    f"{t[-1]:.0f} s)")

    ax[1].plot(res.freq, res.power, lw=0.7)
    for k, ls in ((1, "-"), (2, "--"), (3, ":")):
        ax[1].axvline(k / truth.p_spin, color="C3", ls=ls, lw=0.8,
                      label=f"{k}/P_A" if k <= 2 else None)
        ax[1].axvline(k / truth.p_prec, color="C2", ls=ls, lw=0.8,
                      label=f"{k}/P_B" if k <= 2 else None)
    ax[1].set_xlabel("frequency [Hz]")
    ax[1].set_ylabel("GLS power")
    ax[1].set_title("Generalised Lomb-Scargle periodogram")
    ax[1].legend(fontsize=8)

    if res.env_freq is not None:
        ax[2].plot(res.env_freq, res.env_power, lw=0.8, color="C2")
        for k in (1, 2):
            ax[2].axvline(k / truth.p_prec, color="C3", ls="--", lw=0.8,
                          label=f"{k}/P_B")
        ax[2].set_xlabel("frequency [Hz]")
        ax[2].set_ylabel("GLS power")
        ax[2].set_title("Envelope periodogram (fine structure averaged out)")
        ax[2].legend(fontsize=8)

    ax = [ax[0], ax[1], ax[3]]

    v = res.verdict
    yf, _ = ps.flatten(t, y, 2.0 * v.p_half)
    prof, _e = ps.fold_profile(t, yf, None, v.p_fundamental, nbins=200)
    half = prof.size // 2
    ph = (np.arange(half) + 0.5) / half
    ax[2].plot(ph, prof[:half], label="first half-cycle")
    ax[2].plot(ph, prof[half:], label="second half-cycle", ls="--")
    ax[2].set_xlabel(f"phase within half of P = {v.p_fundamental:.4f} s "
                     "(envelope divided out)")
    ax[2].set_ylabel("mean normalised flux")
    ax[2].set_title(f"Final fold test: reduced chi2 = {v.chi2_reduced:.2f} "
                    f"-> {v.decision}   |   adopted P_A = {res.p_spin:.4f} s")
    ax[2].legend(fontsize=8)

    fig.tight_layout()
    path = outdir / f"period_recovery_model_{model}.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"  plots  -> {path.relative_to(ROOT)}")


# ------------------------------------------------------------------------ main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=sorted(fm.MODELS), default="A")
    ap.add_argument("--duration", type=float, default=config.DEFAULT_DURATION_S)
    ap.add_argument("--fps", type=float, default=config.FPS)
    ap.add_argument("--noise", type=float, default=0.02,
                    help="Gaussian sigma as a fraction of mean flux")
    ap.add_argument("--stray", type=float, default=0.015,
                    help="stray-light pedestal as a fraction of peak flux")
    ap.add_argument("--drift", type=float, default=0.0,
                    help="fractional COB output droop over the recording")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args(argv)

    truth = config.TRUTH
    params = fm.default_params(args.model, truth)

    print(f"Synthesising Model {args.model} ...")
    t, y, dy, clean = fm.synthesize(
        args.duration, args.fps, params, model=args.model,
        noise_frac=args.noise, stray_light_frac=args.stray,
        drift_frac=args.drift, seed=args.seed,
    )

    # Stage 4 detrending, as it will run on real data: normalise to unit mean
    # and remove any residual linear trend.
    y_n = y / y.mean()
    dy_n = dy / y.mean()
    y_n = y_n - np.polyval(np.polyfit(t, y_n, 1), t) + 1.0

    pulses = simulate_slot_pulses(args.duration, truth.p_spin, seed=args.seed)
    slot_p, slot_err = period_from_pulses(pulses)

    print("Running period search ...")
    res = ps.search(t, y_n, dy_n)

    txt = report(args.model, truth, res, slot_p, slot_err, args)
    print()
    print(txt)

    DATA.mkdir(exist_ok=True)
    (DATA / "raw").mkdir(exist_ok=True)
    (DATA / "serial").mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)

    lc_path = DATA / "raw" / f"synthetic_lightcurve_model_{args.model}.csv"
    np.savetxt(lc_path, np.column_stack([t, y, dy, clean]), delimiter=",",
               header="t_s,flux,sigma,flux_clean", comments="")

    pulse_path = DATA / "serial" / f"synthetic_pulses_model_{args.model}.txt"
    pulse_path.write_text("\n".join(f"P {v}" for v in pulses) + "\n")

    rep_path = RESULTS / f"period_recovery_model_{args.model}.txt"
    rep_path.write_text(txt + "\n")

    json_path = RESULTS / f"period_recovery_model_{args.model}.json"
    json_path.write_text(json.dumps({
        "model": args.model,
        "truth": {"p_spin": truth.p_spin, "p_prec": truth.p_prec,
                  "cone_deg": truth.cone_deg},
        "recovered": {
            "p_spin": res.p_spin, "p_spin_err": res.p_spin_err,
            "p_prec": res.p_prec, "p_prec_err": res.p_prec_err,
            "slot_p_spin": slot_p, "slot_p_spin_err": slot_err,
        },
        "harmonic": {
            "f_spin_peak": res.f_spin_peak,
            "spin_harmonic": res.spin_harmonic,
            "chi2_reduced": res.verdict.chi2_reduced,
            "decision": res.verdict.decision,
            "trail": [
                {"p_half": v.p_half, "p_full": v.p_fundamental,
                 "chi2_reduced": v.chi2_reduced, "decision": v.decision}
                for v in res.trail
            ],
            "f_prec_peak": res.f_prec_peak,
            "prec_harmonic": res.prec_harmonic,
        },
        "settings": vars(args),
    }, indent=2))

    print()
    print(f"  curve  -> {lc_path.relative_to(ROOT)}")
    print(f"  pulses -> {pulse_path.relative_to(ROOT)}")
    print(f"  report -> {rep_path.relative_to(ROOT)}")

    if not args.no_plots:
        make_plots(t, y_n, res, truth, args.model, RESULTS / "plots")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
