"""Stages 1-6 end to end, against synthetic data.

    python pipeline/run_pipeline.py                    # full chain through a real .mov
    python pipeline/run_pipeline.py --skip-video       # measure rendered frames directly
    python pipeline/run_pipeline.py --gamma 1.0        # linear camera
    python pipeline/run_pipeline.py --no-linearise     # skip the response inversion

Chain:
  1 forward model      box-wing, Lambertian + specular, spin + precession
    render             light curve -> PNG frames (photon noise, gamma, stray light)
    encode             frames -> CFR .mov
  2 extraction         ffmpeg -vsync 0, with the constant-frame-rate audit
  3 photometry         fixed circular aperture + background annulus
  4 detrend            linearise, normalise to unit mean, remove linear drift
  5 period search      generalised Lomb-Scargle, plus PDM as a shape-free check
  6 harmonic + score   sub-harmonic fold walk, then compare against truth
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import forward_model as fm
import frames as fr
import period as ps
import photometry as ph
import render_frames as rf

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RESULTS = ROOT / "results"


def spearman(a, b):
    """Rank correlation, without pulling in scipy.stats for one number."""
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra -= ra.mean()
    rb -= rb.mean()
    return float((ra @ rb) / np.sqrt((ra @ ra) * (rb @ rb)))


def pearson(a, b):
    a = a - a.mean()
    b = b - b.mean()
    return float((a @ b) / np.sqrt((a @ a) * (b @ b)))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=sorted(fm.MODELS), default="A",
                    help="A = symmetric two-panel body, B = single panel + dish")
    ap.add_argument("--duration", type=float, default=config.DEFAULT_DURATION_S)
    ap.add_argument("--fps", type=float, default=config.FPS)
    ap.add_argument("--size", type=int, nargs=2, default=(160, 120))
    ap.add_argument("--gamma", type=float, default=2.2,
                    help="camera gamma used when rendering")
    ap.add_argument("--no-linearise", action="store_true",
                    help="do not invert the gamma before summing")
    ap.add_argument("--peak-photons", type=float, default=9.0e4)
    ap.add_argument("--stray", type=float, default=0.015)
    ap.add_argument("--skip-video", action="store_true",
                    help="measure the rendered PNGs, skipping encode+extract")
    ap.add_argument("--reuse-frames", action="store_true",
                    help="skip render/encode/extract and re-measure the frames "
                         "already in data/frames/extracted_model_<M>/")
    ap.add_argument("--recentre", action="store_true",
                    help="per-frame aperture recentring (diagnostic; biased)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    truth = config.TRUTH
    t0 = time.time()

    # ---------------------------------------------------------- stage 1
    print("[1] forward model")
    params = fm.default_params(args.model, truth)
    n = int(round(args.duration * args.fps))
    t = np.arange(n) / args.fps
    facets = fm.MODELS[args.model](panel_area=params.panel_area)
    clean = fm.light_curve(t, params, facets)
    phi = 2.0 * np.pi * t / params.p_spin + params.spin_phase0
    print("    {} samples, {:.0f} s at {:.0f} fps".format(n, args.duration, args.fps))
    print("    {}".format(truth.summary()))

    framedir = DATA / "frames" / "rendered_model_{}".format(args.model)

    if args.reuse_frames:
        # Rendering and re-extracting 7200 PNGs is the slow part of this chain
        # and nothing about it changes when only the photometry does. Reusing
        # the frames makes an aperture or gain change a one-minute experiment
        # instead of an hour-long one.
        outdir = DATA / "frames" / "extracted_model_{}".format(args.model)
        paths = sorted(outdir.glob("f*.png"))
        if not paths:
            raise SystemExit("no extracted frames in {}; run without "
                             "--reuse-frames first".format(outdir))
        print("[1b/2] reusing {} extracted frames from {}".format(
            len(paths), outdir.relative_to(ROOT)))
        audit = None
        ts = fr.time_base(len(paths), args.fps)
        return _measure_and_report(args, truth, t, clean, paths, framedir,
                                   audit, ts, t0)

    print("[1b] rendering frames  ({}x{}, gamma {:g})".format(*args.size, args.gamma))
    rendered = rf.render_sequence(
        t, clean, framedir, size=tuple(args.size), phi=phi,
        peak_photons=args.peak_photons, stray_frac=args.stray,
        gamma=args.gamma, seed=args.seed)
    print("    {} frames -> {}".format(len(rendered), framedir.relative_to(ROOT)))

    # ---------------------------------------------------------- stage 2
    if args.skip_video:
        print("[2] extraction SKIPPED; measuring rendered frames directly")
        paths = rendered
        audit = None
    else:
        mov = DATA / "raw" / "synthetic_model_{}.mov".format(args.model)
        print("[1c] encoding {}".format(mov.name))
        rf.encode_video(framedir, mov, fps=args.fps)
        print("    {:.1f} MB".format(mov.stat().st_size / 1e6))

        print("[2] ffmpeg extraction + CFR audit")
        outdir = DATA / "frames" / "extracted_model_{}".format(args.model)
        paths, audit = fr.extract_frames(mov, outdir, fps=args.fps)
        print(audit)
        if not audit.cfr_ok:
            print("    ABORTING: the time base is invalid.")
            return 2
        if len(paths) != n:
            print("    ! {} extracted vs {} rendered".format(len(paths), n))

    ts = fr.time_base(len(paths), args.fps)
    return _measure_and_report(args, truth, t, clean, paths, framedir,
                               audit, ts, t0)


def _measure_and_report(args, truth, t, clean, paths, framedir, audit, ts, t0):

    # ---------------------------------------------------------- stage 3
    print("[3] aperture photometry")
    g = 1.0 if args.no_linearise else args.gamma
    # Gain in photons per unit linearised intensity. The renderer writes the
    # full-well depth it chose, so this is the true photon scale rather than a
    # guess -- which is what makes the quoted errors, and therefore chi2 in the
    # inversion, mean something absolute.
    meta_path = framedir / "render_meta.json"
    gain = 1.0 / 3.0e-5
    if meta_path.exists():
        gain = json.loads(meta_path.read_text())["full_well_photons"]
        print("    gain from render_meta.json: {:.0f} photons/unit".format(gain))
    stack = ph.stack_median(paths, gamma=g)
    aper = ph.find_aperture(stack)
    phot = ph.measure(paths, aper, gamma=g, gain=gain,
                      recentre=args.recentre, progress_every=400)
    print(phot.summary())

    # ---------------------------------------------------------- stage 4
    print("[4] detrend")
    y = phot.flux / phot.flux.mean()
    dy = phot.err / phot.flux.mean()
    y = y - np.polyval(np.polyfit(ts, y, 1), ts) + 1.0

    ref = clean[:len(y)]
    r_lin = pearson(y, ref)
    r_rank = spearman(y, ref)
    a, b = np.polyfit(ref, y, 1)
    resid = y - (a * ref + b)
    frac_rms = float(resid.std() / y.mean())
    print("    photometry vs input light curve:")
    print("      Pearson r  = {:.6f}".format(r_lin))
    print("      Spearman r = {:.6f}".format(r_rank))
    print("      residual RMS after linear rescale = {:.3%} of mean".format(frac_rms))
    if g == 1.0 and args.gamma != 1.0:
        print("      (linearisation disabled: a low Pearson r with a high Spearman r")
        print("       is the gamma curve, and is exactly why period survives but")
        print("       amplitude-derived quantities do not)")

    # ---------------------------------------------------------- stages 5 and 6
    print("[5+6] period search, harmonic resolution")
    res = ps.search(ts, y, dy)

    order = max(round(truth.p_spin / res.p_spin), 1) if res.p_spin > 0 else 1
    m_prec = max(round(truth.p_prec / res.p_prec), 1) if res.p_prec > 0 else 1
    p_spin = order * res.p_spin
    p_prec = m_prec * res.p_prec

    lines = []
    add = lines.append
    add("=" * 72)
    add("SDC-001 pipeline, stages 1-6, synthetic Model {}".format(args.model))
    add("=" * 72)
    add("{:.0f} s at {:.0f} fps, {}x{} px, gamma {:g}{}".format(
        args.duration, args.fps, args.size[0], args.size[1], args.gamma,
        "" if not args.no_linearise else "  (NOT linearised)"))
    if audit is not None:
        add("frames: {} extracted, CFR {}".format(
            audit.n_frames, "PASS" if audit.cfr_ok else "FAIL"))
    add("")
    add("Stage 3 fidelity (measured flux vs the light curve that generated it)")
    add("  Pearson r  {:.6f}    Spearman r  {:.6f}    resid {:.3%}".format(
        r_lin, r_rank, frac_rms))
    add("")
    add("Harmonic resolution")
    add("  strongest spin-band peak : {:.6f} Hz -> {:.5f} s".format(
        res.f_spin_peak, 1.0 / res.f_spin_peak))
    for i, v in enumerate(res.trail, 1):
        add("  round {}: {:.5f} s vs {:.5f} s  chi2_red = {:8.2f} -> {}".format(
            i, v.p_half, v.p_fundamental, v.chi2_reduced, v.decision))
    add("  light-curve repeat period : {:.5f} s".format(res.p_spin))
    add("  target symmetry order     : {}".format(order))
    add("")
    add("Recovered against commanded truth")
    add("  {:<28}{:>19}{:>11}{:>8}".format("quantity", "recovered", "truth", "dev"))
    for name, val, err, tru in [
        ("spin period P_A [s]", p_spin, order * res.p_spin_err, truth.p_spin),
        ("precession P_B [s]", p_prec, m_prec * res.p_prec_err, truth.p_prec),
    ]:
        dev = (val - tru) / err if err > 0 else float("nan")
        add("  {:<28}{:>10.5f} +/- {:<6.5f}{:>11.5f}{:>7.1f}s".format(
            name, val, err, tru, dev))
    if getattr(res, "prec_note", ""):
        add("  ! {}".format(res.prec_note))
    add("")
    add("  P_A error vs commanded : {:.4f} %".format(
        100 * abs(p_spin - truth.p_spin) / truth.p_spin))
    add("  P_B error vs commanded : {:.4f} %".format(
        100 * abs(p_prec - truth.p_prec) / truth.p_prec))
    if order != 1:
        add("")
        add("  Note: the photometry constrains the repeat period to P_A/{}. Model"
            .format(order))
        add("  {} is {}-fold symmetric about the spin axis, so consecutive cycles"
            .format(args.model, order))
        add("  are identical and no photometric method can separate them. The slot")
        add("  sensor picks the branch; Model B breaks the symmetry optically.")
    add("=" * 72)

    txt = "\n".join(lines)
    print()
    print(txt)
    print("elapsed {:.1f} s".format(time.time() - t0))

    RESULTS.mkdir(exist_ok=True)
    # Tag non-default runs so a diagnostic cannot silently overwrite the
    # canonical result for a model. Learned the hard way.
    tag = "pipeline_model_{}".format(args.model)
    if args.duration != config.DEFAULT_DURATION_S:
        tag += "_{:.0f}s".format(args.duration)
    if args.no_linearise:
        tag += "_nolin"
    if args.skip_video:
        tag += "_novideo"
    if args.recentre:
        tag += "_recentre"
    if args.gamma != 2.2:
        tag += "_g{:g}".format(args.gamma)
    (RESULTS / (tag + ".txt")).write_text(txt + "\n")
    np.savetxt(RESULTS / (tag + "_photometry.csv"),
               np.column_stack([ts, phot.flux, phot.err, phot.background,
                                phot.cx, phot.cy, ref]),
               delimiter=",", comments="",
               header="t_s,flux,err,background,cx,cy,truth_flux")
    (RESULTS / (tag + ".json")).write_text(json.dumps({
        "model": args.model,
        "stage3": {"pearson": r_lin, "spearman": r_rank, "resid_frac": frac_rms,
                   "aperture": str(aper), "n_pix": phot.n_pix,
                   "saturated_frames": int(phot.saturated.sum())},
        "recovered": {"p_spin": p_spin, "p_spin_err": order * res.p_spin_err,
                      "p_prec": p_prec, "p_prec_err": m_prec * res.p_prec_err,
                      "symmetry_order": int(order)},
        "truth": {"p_spin": truth.p_spin, "p_prec": truth.p_prec},
        "settings": vars(args),
    }, indent=2, default=str))
    print("  report -> {}".format((RESULTS / (tag + ".txt")).relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
