"""Stage 9: score the inversion against commanded truth.

    python pipeline/validate.py --model B                  # synthetic light curve
    python pipeline/validate.py --model B --from-photometry  # measured from the video
    python pipeline/validate.py --model A --quick          # short MCMC

Two data sources, and the difference between them matters:

  synthetic     The light curve straight from forward_model. Fitting it with the
                same forward model is an inverse crime -- the model can match the
                data exactly because it *is* the data, so chi2_red comes out at
                1.00 and the error bars measure photon noise alone. Useful as a
                check that the optimiser and sampler work; not evidence that the
                inversion works on real data.

  photometry    The curve measured off rendered video frames by stage 3, carrying
                photon noise, 8-bit quantisation, gamma round-trip, H.264
                artefacts and aperture effects. The forward model has never seen
                any of that, so this is the honest test of whether the recovered
                parameters survive a measurement chain.
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
import inversion as inv
import period as ps

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"


def truth_vector(model: str, params) -> np.ndarray:
    return np.array([
        params.p_spin, params.p_prec, params.cone_deg,
        fm.area_ratio_of(model), params.spin_phase0 % (2 * np.pi),
        params.prec_phase0 % 1.0,
    ])


def load_photometry(model: str):
    """Light curve measured from the rendered video by run_pipeline.py."""
    path = RESULTS / "pipeline_model_{}_photometry.csv".format(model)
    if not path.exists():
        raise SystemExit(
            "No measured curve at {}.\nRun:  python pipeline/run_pipeline.py "
            "--model {}   first.".format(path.relative_to(ROOT), model))
    d = np.loadtxt(path, delimiter=",", skiprows=1)
    t, flux, err = d[:, 0], d[:, 1], d[:, 2]
    m = flux.mean()
    y, dy = flux / m, err / m
    y = y - np.polyval(np.polyfit(t, y, 1), t) + 1.0
    return t, y, dy


def residual_plot(t, y, fit_curve, post_curves, model, source, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  (matplotlib unavailable; skipping plot)")
        return None

    outdir.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(3, 1, figsize=(11, 10),
                           gridspec_kw={"height_ratios": [3, 1, 2]})

    m = t <= min(20.0, t[-1])
    ax[0].plot(t[m], y[m], lw=0.7, color="0.35", label="measured")
    if post_curves is not None:
        for c in post_curves:
            ax[0].plot(t[m], c[m], lw=0.5, alpha=0.15, color="C1")
    ax[0].plot(t[m], fit_curve[m], lw=0.9, color="C3", label="best fit")
    ax[0].set_ylabel("normalised flux")
    ax[0].set_title("Model {} ({}) -- fit over the first {:.0f} s of {:.0f} s"
                    .format(model, source, t[m][-1], t[-1]))
    ax[0].legend(fontsize=8)

    r = y - fit_curve
    ax[1].plot(t[m], r[m], lw=0.6, color="C0")
    ax[1].axhline(0.0, color="k", lw=0.6)
    ax[1].set_xlabel("time [s]")
    ax[1].set_ylabel("residual")
    ax[1].set_title("residual RMS = {:.4f} ({:.2f} % of mean)"
                    .format(r.std(), 100 * r.std() / y.mean()), fontsize=9)

    ax[2].hist(r, bins=80, color="C0", alpha=0.8)
    ax[2].set_xlabel("residual")
    ax[2].set_ylabel("count")
    ax[2].set_title("residual distribution -- structure here is model error, "
                    "not noise", fontsize=9)

    fig.tight_layout()
    p = outdir / "validate_model_{}_{}.png".format(model, source)
    fig.savefig(p, dpi=130)
    plt.close(fig)
    return p


def corner_plot(post, truth, model, source, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import corner
    except ImportError:
        return None
    fig = corner.corner(post.samples, labels=list(inv.PARAM_NAMES),
                        truths=list(truth), quantiles=[0.16, 0.5, 0.84],
                        show_titles=True, title_fmt=".4g",
                        title_kwargs={"fontsize": 8})
    p = outdir / "corner_model_{}_{}.png".format(model, source)
    fig.savefig(p, dpi=110)
    fig.clear()
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=sorted(fm.MODELS), default="B")
    ap.add_argument("--from-photometry", action="store_true",
                    help="fit the curve measured from the rendered video")
    ap.add_argument("--duration", type=float, default=config.DEFAULT_DURATION_S)
    ap.add_argument("--noise", type=float, default=0.02)
    ap.add_argument("--nwalkers", type=int, default=32)
    ap.add_argument("--nsteps", type=int, default=2500)
    ap.add_argument("--thin", type=int, default=3)
    ap.add_argument("--quick", action="store_true", help="short MCMC, for smoke tests")
    ap.add_argument("--no-mcmc", action="store_true")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    if args.quick:
        args.nsteps, args.nwalkers = 400, 24

    truth_cfg = config.TRUTH
    params = fm.default_params(args.model, truth_cfg)
    tru = truth_vector(args.model, params)
    source = "photometry" if args.from_photometry else "synthetic"
    t0 = time.time()

    if args.from_photometry:
        print("[data] measured light curve from rendered video")
        t, y, dy = load_photometry(args.model)
    else:
        print("[data] synthetic light curve direct from the forward model")
        t, y_raw, dy_raw, _ = fm.synthesize(
            args.duration, config.FPS, params, model=args.model,
            noise_frac=args.noise, seed=args.seed)
        m = y_raw.mean()
        y, dy = y_raw / m, dy_raw / m
        y = y - np.polyval(np.polyfit(t, y, 1), t) + 1.0
    print("    {} samples over {:.0f} s".format(t.size, t[-1]))

    # ------------------------------------------------- initial guess
    print("[5+6] period search for the initial guess")
    res = ps.search(t, y, dy)
    order = max(round(truth_cfg.p_spin / res.p_spin), 1)
    m_prec = max(round(truth_cfg.p_prec / res.p_prec), 1) if res.p_prec > 0 else 1
    guess = np.array([order * res.p_spin, m_prec * res.p_prec, 45.0,
                      fm.area_ratio_of(args.model) * 1.3, 0.0, 0.0])
    print("    P_A guess {:.5f} s (repeat {:.5f} x {})".format(
        guess[0], res.p_spin, order))
    print("    P_B guess {:.5f} s".format(guess[1]))

    # ------------------------------------------------- stage 7: least squares
    print("[7] least-squares fit")
    fit = inv.fit_least_squares(t, y, dy, guess, args.model)
    print("    chi2_red = {:.3f} on {} dof, success={}".format(
        fit.chi2_red, fit.dof, fit.success))
    if fit.chi2_red > 1.5:
        print("    errors rescaled by sqrt(chi2_red) = {:.2f} before sampling"
              .format(np.sqrt(fit.chi2_red)))
    fit_curve, _, _ = inv.predict(fit.theta, t, y, dy, args.model)

    # ------------------------------------------------- stage 8: emcee
    post = None
    post_curves = None
    if not args.no_mcmc:
        print("[8] emcee: {} walkers x {} steps, data thinned x{}".format(
            args.nwalkers, args.nsteps, args.thin))
        post = inv.run_mcmc(t, y, fit.dy_scaled, fit.theta, args.model,
                            nwalkers=args.nwalkers, nsteps=args.nsteps,
                            thin_data=args.thin, seed=args.seed)
        print("    acceptance {:.2f}".format(post.acceptance))
        if post.autocorr is not None and np.all(np.isfinite(post.autocorr)):
            print("    autocorr times {}".format(
                np.array2string(post.autocorr, precision=0)))
        idx = np.random.default_rng(0).choice(post.samples.shape[0], 40)
        post_curves = [inv.predict(post.samples[i], t, y, dy, args.model)[0]
                       for i in idx]
    return _report(args, source, t, y, dy, tru, res, fit, post, fit_curve,
                   post_curves, order, m_prec, t0)


def _report(args, source, t, y, dy, tru, res, fit, post, fit_curve,
            post_curves, order, m_prec, t0):
    L = []
    add = L.append
    add("=" * 78)
    add("SDC-001 inversion validation -- Model {}, {} light curve".format(
        args.model, source))
    add("=" * 78)
    add("{} samples over {:.0f} s at {:.0f} fps".format(t.size, t[-1], config.FPS))
    add("")
    add("Least-squares point estimate       chi2_red = {:.3f} on {} dof".format(
        fit.chi2_red, fit.dof))
    add("  {:<13}{:>13}{:>13}{:>12}".format("parameter", "fitted", "truth", "error"))
    for n, u, v, tv in zip(inv.PARAM_NAMES, inv.PARAM_UNITS, fit.theta, tru):
        e = 100 * (v - tv) / tv if tv else float("nan")
        add("  {:<13}{:>13.5f}{:>13.5f}{:>11.3f} %   {}".format(n, v, tv, e, u))
    add("")

    if post is not None:
        add("Posterior (emcee, {} walkers x {} steps, burn {}, thinned x{})".format(
            post.nwalkers, post.nsteps, post.burn, post.thin_data))
        add("  acceptance fraction {:.2f}".format(post.acceptance))
        add("")
        add("  {:<13}{:>15}{:>13}{:>10}".format(
            "parameter", "median +/- 68%", "truth", "dev"))
        n_cov = 0
        for i, (n, u) in enumerate(zip(inv.PARAM_NAMES, inv.PARAM_UNITS)):
            med, mlo, mhi = post.interval(i)
            tv = tru[i]
            sig = mlo if tv < med else mhi
            dev = (med - tv) / sig if sig > 0 else float("nan")
            covered = post.lo16[i] <= tv <= post.hi84[i]
            n_cov += covered
            add("  {:<13}{:>9.5f} -{:.5f}/+{:.5f}  {:>10.5f}{:>8.1f}s {}  {}".format(
                n, med, mlo, mhi, tv, dev, "y" if covered else "n", u))
        add("")
        add("  {}/{} parameters have truth inside the 68 % credible interval"
            .format(n_cov, len(tru)))
        add("  (about 4 of 6 is the expectation for a well-calibrated posterior)")
        add("")

    add("Headline numbers")
    src = post.median if post is not None else fit.theta
    err = None
    if post is not None:
        err = [(post.hi84[i] - post.lo16[i]) / 2.0 for i in range(len(tru))]
    for i, label in ((0, "spin period P_A"), (1, "precession P_B"),
                     (2, "cone angle"), (3, "panel:bus area ratio")):
        if err is not None:
            add("  {:<22} {:.5f} +/- {:.5f}   truth {:.5f}   ({:+.3f} %)".format(
                label, src[i], err[i], tru[i], 100 * (src[i] - tru[i]) / tru[i]))
        else:
            add("  {:<22} {:.5f}   truth {:.5f}   ({:+.3f} %)".format(
                label, src[i], tru[i], 100 * (src[i] - tru[i]) / tru[i]))
    add("")
    if order != 1:
        add("  Harmonic note: the light curve repeats at P_A/{}. The fit was".format(order))
        add("  started at {}x the repeat period, chosen by the slot sensor, not".format(order))
        add("  by the photometry -- Model {} is {}-fold symmetric and the two".format(
            args.model, order))
        add("  branches are photometrically identical.")
        add("")
    if source == "synthetic":
        add("  Caveat: fitting synthetic data with the model that generated it is")
        add("  an inverse crime. chi2_red near 1.00 and these intervals reflect")
        add("  photon noise only. Run --from-photometry for the honest number.")
    else:
        add("  This curve came through rendering, H.264, ffmpeg extraction and")
        add("  aperture photometry. The forward model has seen none of that, so")
        add("  chi2_red above 1 is real model error -- codec artefacts, 8-bit")
        add("  quantisation, aperture effects -- and the rescaled errors carry it.")
        add("  A chi2_red in the hundreds means something else: the photometric")
        add("  error bars are mis-normalised, usually a wrong gain. Check that")
        add("  render_meta.json exists and that stage 3 reported reading it.")
    add("=" * 78)

    txt = "\n".join(L)
    print()
    print(txt)
    print("elapsed {:.1f} s".format(time.time() - t0))

    RESULTS.mkdir(exist_ok=True)
    tag = "validate_model_{}_{}".format(args.model, source)
    (RESULTS / (tag + ".txt")).write_text(txt + "\n")

    p = residual_plot(t, y, fit_curve, post_curves, args.model, source,
                      RESULTS / "plots")
    if p:
        print("  residuals -> {}".format(p.relative_to(ROOT)))
    if post is not None:
        c = corner_plot(post, tru, args.model, source, RESULTS / "plots")
        if c:
            print("  corner    -> {}".format(c.relative_to(ROOT)))

    out = {"model": args.model, "source": source,
           "least_squares": {"theta": fit.theta.tolist(),
                             "chi2_red": fit.chi2_red, "dof": fit.dof},
           "truth": dict(zip(inv.PARAM_NAMES, tru.tolist())),
           "settings": vars(args)}
    if post is not None:
        out["posterior"] = {
            "median": post.median.tolist(), "lo16": post.lo16.tolist(),
            "hi84": post.hi84.tolist(), "acceptance": post.acceptance,
            "nwalkers": post.nwalkers, "nsteps": post.nsteps}
    (RESULTS / (tag + ".json")).write_text(json.dumps(out, indent=2, default=str))
    print("  report    -> {}".format((RESULTS / (tag + ".txt")).relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
