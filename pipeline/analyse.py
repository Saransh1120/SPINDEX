"""Feature extraction, anomaly detection and importance-gated notification.

    python pipeline/analyse.py --csv data/serial/run01.csv --expect 64.0
    python pipeline/analyse.py --session data/sessions/run01
    python pipeline/analyse.py --csv run.csv --policy unattended --export out/

Reads a light curve from any of the project's sources, extracts the full feature
vector, runs the anomaly detectors over it, gates the resulting events through
the importance policy, and writes a checksummed session archive.

Sources
-------
--csv       a live.py --record file (t_perf_s, flux, background, saturated)
            or a run_pipeline photometry file (t_s, flux, err, ...)
--session   an existing session archive, re-analysed
--synthetic a generated curve, for checking the tooling without hardware

Segmented analysis
------------------
With --segments N the record is split into N consecutive windows and the
features are extracted from each in turn, feeding the drift detectors. This is
what surfaces *changes* -- a spin-down, a lamp drooping, a target drifting out of
the aperture -- rather than only whole-record properties. One window gives you a
description; several give you a history.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anomaly
import features as featmod
import importance as imp
import session as sessionmod

ROOT = Path(__file__).resolve().parent.parent


def load_csv(path):
    """Load a light curve from either of the project's CSV layouts."""
    path = Path(path)
    with open(path, newline="") as fh:
        header = next(csv.reader(fh))
    cols = [c.strip().lower() for c in header]
    d = np.genfromtxt(path, delimiter=",", skip_header=1, invalid_raise=False)
    if d.ndim == 1:
        d = d.reshape(1, -1)

    def col(*names, default=None):
        for nm in names:
            if nm in cols:
                return d[:, cols.index(nm)]
        return default

    t = col("t_perf_s", "t_s", "t", "time")
    flux = col("flux")
    if t is None or flux is None:
        raise SystemExit(
            "could not find time and flux columns in {}.\nHeader was: {}"
            .format(path, header))
    t = t - t[0]
    bg = col("background", default=np.zeros_like(t))
    sat = col("saturated", default=np.zeros_like(t))
    good = np.isfinite(t) & np.isfinite(flux)
    return t[good], flux[good], bg[good], sat[good].astype(bool)


def synth(period=64.0, span=600.0, fps=30.0, noise=0.05, seed=0,
          drift_at=None, drift_to=None):
    rng = np.random.default_rng(seed)
    n = int(span * fps)
    t = np.arange(n) / fps
    p = np.full(n, float(period))
    if drift_at is not None and drift_to is not None:
        p[t >= drift_at] = float(drift_to)
    phase = np.cumsum(2 * np.pi / p / fps)
    y = 1.0 + 0.35 * np.sin(2 * phase + 0.3) + 0.12 * np.sin(phase)
    y = y + rng.normal(0.0, noise, n)
    return t, y, np.full(n, 0.01), np.zeros(n, bool)


def format_report(fs, decisions, stats, args, expect=None, segments=None,
                  import_events=None) -> str:
    L = []
    add = L.append
    W = 78
    add("=" * W)
    add("SDC-001  feature extraction, anomaly detection, notification")
    add("=" * W)
    add("source     {}".format(args.csv or args.session or "synthetic"))
    add("policy     {}  (threshold {:.2f}, cooldown {:.0f}s, floor '{}')".format(
        args.policy, imp.PRESETS[args.policy].threshold,
        imp.PRESETS[args.policy].cooldown_s, imp.PRESETS[args.policy].min_severity))
    add("samples    {}   span {:.1f} s".format(fs.n_samples, fs.span_s))
    add("")

    if import_events:
        add("-" * W)
        add("IMPORT VALIDATION")
        add("-" * W)
        for e in import_events:
            add("  " + e.one_line())
        add("")

    add("-" * W)
    add("FEATURES")
    add("-" * W)
    groups = [
        ("periodicity", ["period_s", "frequency_hz", "ls_peak_period_s",
                         "harmonic_order", "ls_power", "fap", "peak_q",
                         "harmonic_ratio", "spectral_entropy",
                         "n_significant_peaks"]),
        ("morphology", ["n_glints", "modulation_depth", "amplitude",
                        "duty_cycle", "glint_sharpness", "profile_skew",
                        "profile_asymmetry", "fold_scatter"]),
        ("photometry", ["mean_flux", "flux_std", "snr", "saturated_fraction",
                        "background_level", "background_drift",
                        "baseline_slope_per_s"]),
        ("stability", ["period_drift_s_per_s", "amplitude_drift_per_s",
                       "cadence_hz", "cadence_jitter_ms", "dropout_fraction"]),
    ]
    d = fs.to_dict()
    for gname, keys in groups:
        add("  {}".format(gname))
        for k in keys:
            v = d.get(k)
            vs = "  n/a" if v is None or (isinstance(v, float) and not np.isfinite(v)) \
                else ("{:>12d}".format(v) if isinstance(v, int)
                      else "{:>12.5g}".format(v))
            add("    {:<24}{}   {}".format(k, vs,
                                           featmod.FEATURE_DESCRIPTIONS.get(k, "")[:34]))
        add("")

    if expect:
        n = max(round(expect / fs.period_s), 1) if fs.period_s > 0 else 1
        err = 100 * abs(n * fs.period_s - expect) / expect
        add("-" * W)
        add("AGAINST COMMANDED RATE")
        add("-" * W)
        add("  commanded period   {:.4f} s".format(expect))
        add("  recovered          {:.4f} s   (x{} -> {:.4f} s)".format(
            fs.period_s, n, n * fs.period_s))
        add("  error              {:.3f} %".format(err))
        if n != 1:
            add("  note: the light curve repeats at 1/{} of the commanded period,".format(n))
            add("        which is expected for a target with {}-fold symmetry.".format(n))
        add("  This is the commanded step rate, not an independent measurement:")
        add("  it cannot detect a stalled or skipping motor. With no slot sensor")
        add("  fitted, a stall would read as a clean period at the wrong value.")
        add("")

    if segments:
        add("-" * W)
        add("SEGMENTED HISTORY")
        add("-" * W)
        add("  {:>4} {:>9} {:>10} {:>9} {:>8} {:>7}".format(
            "seg", "t_mid", "period", "amp", "snr", "glints"))
        for i, (tm, sfs) in enumerate(segments):
            add("  {:>4} {:>9.1f} {:>10.4f} {:>9.4f} {:>8.1f} {:>7d}".format(
                i, tm, sfs.period_s, sfs.amplitude, sfs.snr, sfs.n_glints))
        add("")

    add("-" * W)
    add("NOTIFICATIONS")
    add("-" * W)
    fired = [x for x in decisions if x.fired]
    if not decisions:
        add("  no events detected - nothing crossed a detector threshold")
    else:
        add("  {} event(s) -> {} notification(s), {} suppressed ({:.0%})".format(
            stats["events"], stats["notified"], stats["suppressed"],
            stats["suppression_rate"]))
        add("")
        if fired:
            add("  NOTIFIED")
            for x in fired:
                add("    [{:^8}] {:<22} imp {:.2f}  urg {}".format(
                    x.event.severity.upper(), x.event.kind, x.importance, x.urgency))
                add("        {}".format(x.event.message))
                add("        why: {}".format(x.reason))
        muted = [x for x in decisions if not x.fired]
        if muted:
            add("")
            add("  SUPPRESSED  (recorded in the archive, not surfaced)")
            seen = {}
            for x in muted:
                seen.setdefault(x.event.kind, []).append(x)
            for kind, xs in sorted(seen.items()):
                add("    {:<24} x{:<4} {}".format(kind, len(xs), xs[0].reason[:40]))
    add("")
    add("=" * W)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="light-curve CSV from live.py --record or run_pipeline")
    src.add_argument("--session", help="existing session archive to re-analyse")
    src.add_argument("--synthetic", action="store_true", help="generated curve")

    ap.add_argument("--expect", type=float,
                    help="commanded period in seconds, to score against")
    ap.add_argument("--policy", choices=sorted(imp.PRESETS), default="bench")
    ap.add_argument("--policy-file", help="JSON policy file, overrides --policy")
    ap.add_argument("--segments", type=int, default=6,
                    help="split the record into N windows to expose drift (0 = off)")
    ap.add_argument("--fmin", type=float, default=None)
    ap.add_argument("--fmax", type=float, default=5.0)
    ap.add_argument("--export", help="write a session archive to this directory")
    ap.add_argument("--json", help="write the machine-readable result here")
    ap.add_argument("--quiet", action="store_true", help="notifications only")

    # synthetic controls
    ap.add_argument("--syn-period", type=float, default=64.0)
    ap.add_argument("--syn-span", type=float, default=600.0)
    ap.add_argument("--syn-drift-at", type=float)
    ap.add_argument("--syn-drift-to", type=float)
    args = ap.parse_args(argv)

    import_events = []
    if args.csv:
        t, y, bg, sat = load_csv(args.csv)
        source = str(args.csv)
    elif args.session:
        data, import_events = sessionmod.import_session(args.session)
        t, y = data["t"] - data["t"][0], data["flux"]
        bg, sat = data["background"], data["saturated"]
        source = str(args.session)
    else:
        t, y, bg, sat = synth(args.syn_period, args.syn_span,
                              drift_at=args.syn_drift_at,
                              drift_to=args.syn_drift_to)
        source = "synthetic"

    span = float(t[-1] - t[0])
    fmin = args.fmin if args.fmin is not None else 3.0 / span
    policy = (imp.Policy.from_file(args.policy_file) if args.policy_file
              else imp.PRESETS[args.policy])
    # Batch analysis replays a recording far faster than it happened, so a
    # wall-clock cooldown would expire never (all windows land in the same
    # second) and every repeat would be suppressed as "1s elapsed". Drive the
    # engine from the data's own timeline instead, so a 30 s cooldown means
    # 30 s of recording, not 30 s of CPU.
    data_clock = {"t": float(t[0])}
    engine = imp.NotificationEngine(policy, clock=lambda: data_clock["t"])

    # Import problems are gated by the same engine as everything else -- one
    # bar, one audit trail, regardless of where the event came from.
    decisions = engine.submit_all(import_events)

    fs = featmod.extract(t, y, fmin=fmin, fmax=args.fmax,
                         background=bg, saturated=sat)

    segments = []
    if args.segments and args.segments > 1 and t.size > 64 * args.segments:
        # Overlapping windows, not disjoint segments. Two reasons: a drift
        # detector needs more observations than a short record can supply if
        # each sample must be independent, and each window must still be long
        # enough to hold several rotations or its period estimate is noise.
        # 50 % overlap doubles the observation count for the detector at the
        # cost of correlated neighbours -- an acceptable trade when the
        # alternative is a detector that never arms at all.
        det = anomaly.DriftDetector(warmup=2, min_history=4)
        n_win = args.segments
        win = 2.0 * span / (n_win + 1)
        step = win / 2.0
        edges = [(t[0] + i * step, t[0] + i * step + win) for i in range(n_win)]
        for lo, hi in edges:
            m = (t >= lo) & (t < hi)
            if m.sum() < 64:
                continue
            seg_span = float(t[m][-1] - t[m][0])
            sfs = featmod.extract(t[m], y[m], fmin=3.0 / seg_span, fmax=args.fmax,
                                  background=bg[m], saturated=sat[m])
            segments.append((0.5 * (lo + hi), sfs))
            data_clock["t"] = float(0.5 * (lo + hi))
            evs = anomaly.detect(sfs, det, source=source)
            engine.observe_absent([e.kind for e in evs])
            decisions += engine.submit_all(evs)
    else:
        evs = anomaly.detect(fs, None, source=source)
        decisions += engine.submit_all(evs)

    stats = engine.stats()
    report = format_report(fs, decisions, stats, args, expect=args.expect,
                           segments=segments, import_events=import_events)
    if args.quiet:
        for d in decisions:
            if d.fired:
                print(d.one_line())
    else:
        print(report)

    if args.export:
        out = sessionmod.export_session(
            args.export, t, y, background=bg, saturated=sat, features=fs,
            events=[d.event for d in decisions], decisions=decisions,
            policy=policy, source=source,
            notes="analyse.py {}".format(time.strftime("%Y-%m-%d %H:%M:%S")))
        print("session archive -> {}".format(out))

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "source": source,
            "features": fs.to_dict(),
            "policy": policy.to_dict(),
            "stats": stats,
            "decisions": [d.to_dict() for d in decisions],
        }, indent=2))
        print("json -> {}".format(p))

    # Non-zero exit if anything critical was notified, so this can gate a script.
    return 2 if any(d.fired and d.event.severity in ("critical", "high")
                    for d in decisions) else 0


if __name__ == "__main__":
    raise SystemExit(main())
