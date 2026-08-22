"""Live dashboard for pipeline/live.py -- seven panels, updated in place.

Layout
------
    +----------------+---------------------------------+
    | camera + aper. | scrolling light curve           |
    +----------------+----------------+----------------+
    | P = 1.8497 s   | periodogram    | phase fold     |
    | diagnostics    +----------------+----------------+
    |                | period history | pixel levels   |
    +----------------+----------------+----------------+

Each panel answers a question you would otherwise have to guess at:

  camera        Is the target inside the aperture at every attitude, and is the
                background annulus clear of it?
  light curve   Are there glints at all, and is the modulation depth sane?
  periodogram   Is there one dominant peak or a forest? Harmonic markers show
                whether the adopted period explains the structure.
  phase fold    The strongest evidence the period is right: a correct period
                folds into one clean profile, a wrong one smears.
  history       Is the answer stable, or wandering as the buffer fills?
  levels        Is the exposure sane -- not clipped, not buried in the floor?

Every panel is drawn defensively. A live tool that throws because one array is
briefly empty is worse than useless at the bench.
"""

from __future__ import annotations

import sys
import time
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import period as ps


def _positive_unit_mean(y):
    """Shift and scale a curve so fold/flatten helpers behave.

    They divide by a boxcar mean, which is catastrophic on a zero-mean series.
    """
    y = np.asarray(y, float)
    y = y - y.min() + 1e-9
    m = y.mean()
    return y / m if m > 0 else y


def run_display(state, source, aperture, args):
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from matplotlib.patches import Circle

    fig = plt.figure(figsize=(15, 9))
    fig.canvas.manager.set_window_title("SDC-001 live spin monitor")
    gs = fig.add_gridspec(3, 5, width_ratios=[1.0, 0.95, 0.95, 1.05, 1.25],
                          height_ratios=[1.15, 1, 1],
                          hspace=0.42, wspace=0.30,
                          left=0.035, right=0.995, top=0.945, bottom=0.06)

    ax_cam = fig.add_subplot(gs[0, 0])
    ax_lc = fig.add_subplot(gs[0, 1:3])
    ax_txt = fig.add_subplot(gs[1:3, 0])
    ax_pg = fig.add_subplot(gs[1, 1])
    ax_fold = fig.add_subplot(gs[1, 2])
    ax_hist = fig.add_subplot(gs[2, 1])
    ax_lev = fig.add_subplot(gs[2, 2])
    # The feature list is ~28 lines and the notification feed only needs a few
    # entries, so features take two thirds of the right column and the feed one.
    ax_feat = fig.add_subplot(gs[0:2, 3])    # feature readout
    ax_notif = fig.add_subplot(gs[2, 3])     # notification feed
    ax_log = fig.add_subplot(gs[:, 4])       # full-height activity log

    # ---------------------------------------------------------------- camera
    with state.lock:
        frame0 = state.frame
    im = ax_cam.imshow(frame0, cmap="gray", vmin=0, vmax=1,
                       interpolation="nearest")
    for r, ls in ((aperture.r, "-"), (aperture.r_in, ":"), (aperture.r_out, ":")):
        ax_cam.add_patch(Circle((aperture.x, aperture.y), r, fill=False,
                                color="lime", ls=ls, lw=1.1))
    ax_cam.set_xticks([])
    ax_cam.set_yticks([])
    ax_cam.set_title("{} source  ({}x{})".format(
        source.name, frame0.shape[1], frame0.shape[0]), fontsize=9)

    # ----------------------------------------------------------- light curve
    (lc_line,) = ax_lc.plot([], [], lw=0.8, color="C0")
    ax_lc.set_xlabel("time before now [s]", fontsize=8)
    ax_lc.set_ylabel("flux [arb.]", fontsize=8)
    ax_lc.set_title("scrolling light curve, last {:.0f} s".format(args.window),
                    fontsize=9)
    ax_lc.grid(alpha=0.25)
    ax_lc.tick_params(labelsize=8)

    # ------------------------------------------------------------- read-out
    ax_txt.axis("off")
    big = ax_txt.text(0.5, 0.93, "P = --", ha="center", va="top", fontsize=27,
                      family="monospace", transform=ax_txt.transAxes)
    verdict = ax_txt.text(0.5, 0.845, "", ha="center", va="top", fontsize=9,
                          family="monospace", transform=ax_txt.transAxes)
    sub = ax_txt.text(0.02, 0.78, "", ha="left", va="top", fontsize=8.5,
                      family="monospace", transform=ax_txt.transAxes,
                      linespacing=1.7)

    # ----------------------------------------------------------- periodogram
    (pg_line,) = ax_pg.plot([], [], lw=0.8, color="C2")
    harm_lines = [ax_pg.axvline(np.nan, color="C3", lw=0.9,
                                ls="-" if k == 1 else "--", alpha=0.85 if k == 1 else 0.5)
                  for k in range(1, 5)]
    env_line = ax_pg.axvline(np.nan, color="C1", lw=0.9, ls=":")
    ax_pg.set_xlabel("frequency [Hz]", fontsize=8)
    ax_pg.set_ylabel("LS power", fontsize=8)
    ax_pg.set_title("Lomb-Scargle (real timestamps)", fontsize=9)
    ax_pg.grid(alpha=0.25)
    ax_pg.tick_params(labelsize=8)

    # ------------------------------------------------------------ phase fold
    (fold_line,) = ax_fold.plot([], [], lw=1.0, color="C0")
    (fold_pts,) = ax_fold.plot([], [], ".", ms=1.0, color="0.7", alpha=0.4,
                               zorder=0)
    ax_fold.set_xlabel("phase", fontsize=8)
    ax_fold.set_ylabel("mean flux", fontsize=8)
    ax_fold.set_title("folded at adopted period", fontsize=9)
    ax_fold.grid(alpha=0.25)
    ax_fold.tick_params(labelsize=8)
    ax_fold.set_xlim(0, 1)

    # --------------------------------------------------------- period history
    (hist_line,) = ax_hist.plot([], [], "-", lw=0.9, color="C4")
    (hist_pts,) = ax_hist.plot([], [], ".", ms=3, color="C4")
    ax_hist.set_xlabel("elapsed [s]", fontsize=8)
    ax_hist.set_ylabel("period [s]", fontsize=8)
    ax_hist.set_title("recovered period vs time", fontsize=9)
    ax_hist.grid(alpha=0.25)
    ax_hist.tick_params(labelsize=8)

    # ------------------------------------------------------------ pixel levels
    ax_lev.set_xlabel("pixel value", fontsize=8)
    ax_lev.set_ylabel("count", fontsize=8)
    ax_lev.set_title("levels in aperture", fontsize=9)
    ax_lev.grid(alpha=0.25)
    ax_lev.tick_params(labelsize=8)
    ax_lev.set_yscale("log")

    # --- feature panel
    ax_feat.axis("off")
    ax_feat.set_title("features", fontsize=9, loc="left")
    feat_txt = ax_feat.text(0.0, 0.97, "", ha="left", va="top", fontsize=6.9,
                            family="monospace", transform=ax_feat.transAxes,
                            linespacing=1.42)

    # --- notification feed
    ax_notif.axis("off")
    ax_notif.set_title("notifications  (importance-gated)", fontsize=9, loc="left")
    notif_txt = ax_notif.text(0.0, 0.985, "", ha="left", va="top", fontsize=6.8,
                              family="monospace", transform=ax_notif.transAxes,
                              linespacing=1.35)

    # --- activity log: a scrolling terminal down the right-hand edge.
    # Newest at the bottom, like a terminal, so the eye lands where a terminal
    # user expects it to. Rendered on a dark panel so it reads as a log rather
    # than as another chart.
    ax_log.set_facecolor("#14161a")
    ax_log.set_xticks([])
    ax_log.set_yticks([])
    for spine in ax_log.spines.values():
        spine.set_color("#3a3f47")
    ax_log.set_title("activity log", fontsize=9, loc="left")
    log_txt = ax_log.text(0.015, 0.988, "", ha="left", va="top", fontsize=6.1,
                          family="monospace", transform=ax_log.transAxes,
                          color="#cfd6e0", linespacing=1.30)

    SEV_COLOUR = {"info": "0.45", "low": "0.25", "medium": "#b26a00",
                  "high": "#c1440e", "critical": "#8b0000"}

    history = deque(maxlen=4000)
    last_solution_t = [0.0]
    t_start = time.perf_counter()

    def update(_):
        with state.lock:
            frame = state.frame
            sol = state.solution
            n_frames, n_sat, bg = state.n_frames, state.n_sat, state.bg
            notes = list(state.notes)
        t, y = state.snapshot()
        now = time.perf_counter()

        if frame is not None:
            im.set_data(frame)

        # -------------------------------------------------- light curve
        cadence = jitter = float("nan")
        if t.size > 2:
            lc_line.set_data(t - t[-1], y)
            ax_lc.set_xlim(-args.window, 0.5)
            lo, hi = float(np.min(y)), float(np.max(y))
            pad = 0.08 * (hi - lo) + 1e-9
            ax_lc.set_ylim(lo - pad, hi + pad)
            dt = np.diff(t)
            if dt.size:
                cadence, jitter = float(np.median(dt)), float(np.std(dt))

        # -------------------------------------------------- periodogram
        if sol.freq is not None and sol.spec is not None and sol.freq.size > 2:
            pg_line.set_data(sol.freq, sol.spec)
            ax_pg.set_xlim(sol.freq[0], sol.freq[-1])
            ax_pg.set_ylim(0, max(float(np.max(sol.spec)) * 1.15, 1e-3))
            for k, ln in enumerate(harm_lines, start=1):
                f = k / sol.p_adopted if np.isfinite(sol.p_adopted) and sol.p_adopted > 0 else np.nan
                ln.set_xdata([f, f])
            fe = sol.f_env if np.isfinite(sol.f_env) else np.nan
            env_line.set_xdata([fe, fe])

        # -------------------------------------------------- phase fold
        if np.isfinite(sol.p_adopted) and sol.p_adopted > 0 and t.size > 60:
            try:
                tt = t - t[0]
                yp = _positive_unit_mean(y)
                prof, _err = ps.fold_profile(tt, yp, None, sol.p_adopted, nbins=60)
                ph = (np.arange(prof.size) + 0.5) / prof.size
                good = np.isfinite(prof)
                fold_line.set_data(ph[good], prof[good])
                fold_pts.set_data((tt / sol.p_adopted) % 1.0, yp)
                fin = prof[good]
                if fin.size:
                    lo, hi = float(fin.min()), float(fin.max())
                    span = max(hi - lo, 1e-6)
                    ax_fold.set_ylim(lo - 0.35 * span, hi + 0.35 * span)
            except Exception:
                pass

        # -------------------------------------------------- period history
        if sol.t_computed > last_solution_t[0] and np.isfinite(sol.p_adopted):
            last_solution_t[0] = sol.t_computed
            history.append((now - t_start, sol.p_adopted))
        if len(history) > 1:
            hx = np.array([h[0] for h in history])
            hy = np.array([h[1] for h in history])
            hist_line.set_data(hx, hy)
            hist_pts.set_data(hx, hy)
            ax_hist.set_xlim(0, max(hx[-1] * 1.05, 10))
            lo, hi = float(hy.min()), float(hy.max())
            pad = max(0.06 * (hi - lo), 0.02 * max(hi, 1e-9))
            ax_hist.set_ylim(lo - pad, hi + pad)

        # -------------------------------------------------- pixel levels
        if frame is not None:
            try:
                vals = frame[state.ap_mask] if getattr(state, "ap_mask", None) is not None \
                    else frame.ravel()
                ax_lev.clear()
                ax_lev.hist(vals * 255.0, bins=64, range=(0, 255),
                            color="C0", alpha=0.85)
                ax_lev.axvline(255 * 0.99, color="C3", lw=1.0, ls="--")
                ax_lev.set_yscale("log")
                ax_lev.set_xlabel("pixel value", fontsize=8)
                ax_lev.set_title("levels in aperture   peak {:.0f}/255".format(
                    float(vals.max()) * 255), fontsize=9)
                ax_lev.grid(alpha=0.25)
                ax_lev.tick_params(labelsize=8)
            except Exception:
                pass

        # -------------------------------------------------- read-out
        significant = np.isfinite(sol.fap) and sol.fap < 0.01
        if np.isfinite(sol.p_adopted):
            big.set_text("P = {:.4f} s".format(sol.p_adopted))
            big.set_color("black" if significant else "0.62")
            if significant:
                verdict.set_text("significant  ({:.2f} rpm)".format(
                    60.0 / sol.p_adopted))
                verdict.set_color("C2")
            else:
                verdict.set_text("NOT significant - FAP {:.2f}".format(sol.fap))
                verdict.set_color("C3")
        else:
            big.set_text("P = --")
            big.set_color("0.62")
            verdict.set_text(sol.note or "acquiring")
            verdict.set_color("0.4")

        lines = [
            "LS peak    {:>10}  {:>8}".format(
                "{:.4f} s".format(sol.p_peak) if np.isfinite(sol.p_peak) else "--",
                "{:.4f}Hz".format(sol.f_peak) if np.isfinite(sol.f_peak) else "--"),
            "harmonic   x{:<9g} power {:.3f}".format(
                sol.harmonic if np.isfinite(sol.harmonic) else float("nan"),
                sol.power),
            "envelope   {:>10}".format(
                "{:.3f} s".format(sol.p_env) if np.isfinite(sol.p_env) else "--"),
            "FAP        {:>10.2e}".format(sol.fap),
            "",
            "buffer     {:>5d} pts / {:.1f} s".format(sol.n_samples, sol.span),
            "cadence    {:>6.2f} fps".format(
                1.0 / cadence if cadence and np.isfinite(cadence) else float("nan")),
            "jitter     {:>6.2f} ms".format(
                1e3 * jitter if np.isfinite(jitter) else float("nan")),
            "background {:>8.5f}".format(bg),
            "elapsed    {:>6.0f} s   {} frames".format(now - t_start, n_frames),
        ]
        if n_sat:
            lines += ["", "SATURATED in {} frames".format(n_sat),
                      "  glint peaks are clipped;",
                      "  shorten the exposure"]
        if sol.note and np.isfinite(sol.p_adopted):
            lines += ["", sol.note]
        for w in notes:
            lines += [""] + _wrap(w, 34)
        sub.set_text("\n".join(lines))

        # -------------------------------------------------- features panel
        with state.lock:
            fs = state.featureset
            notifs = list(state.notifications)
            engine = state.engine
            expect = state.expect_period
        if fs is not None:
            d = fs.to_dict()

            def row(label, key, fmt="{:.4g}"):
                v = d.get(key)
                txt = "n/a" if v is None or (isinstance(v, float)
                                             and not np.isfinite(v)) else fmt.format(v)
                return "{:<15}{:>11}".format(label, txt)

            lines = ["PERIODICITY",
                     row("period s", "period_s", "{:.4f}"),
                     row("raw peak s", "ls_peak_period_s", "{:.4f}"),
                     row("harmonic", "harmonic_order", "x{:.0f}"),
                     row("LS power", "ls_power", "{:.3f}"),
                     row("peak Q", "peak_q", "{:.1f}"),
                     row("entropy", "spectral_entropy", "{:.3f}"),
                     row("harm ratio", "harmonic_ratio", "{:.2f}"),
                     "", "MORPHOLOGY",
                     row("glints/rev", "n_glints", "{:.0f}"),
                     row("mod depth", "modulation_depth", "{:.3f}"),
                     row("duty cycle", "duty_cycle", "{:.3f}"),
                     row("sharpness", "glint_sharpness", "{:.2f}"),
                     row("asymmetry", "profile_asymmetry", "{:.3f}"),
                     "", "QUALITY",
                     row("SNR", "snr", "{:.1f}"),
                     row("saturated", "saturated_fraction", "{:.2%}"),
                     row("bkg level", "background_level", "{:.4g}"),
                     row("drift /s", "baseline_slope_per_s", "{:+.2e}"),
                     row("dropouts", "dropout_fraction", "{:.2%}")]
            if expect and np.isfinite(d.get("period_s", np.nan)) and d["period_s"] > 0:
                n = max(round(expect / d["period_s"]), 1)
                err = 100 * abs(n * d["period_s"] - expect) / expect
                lines += ["", "VS COMMANDED",
                          "{:<15}{:>11.4f}".format("commanded s", expect),
                          "{:<15}{:>11}".format("harmonic", "x{}".format(n)),
                          "{:<15}{:>10.3f}%".format("error", err)]
            feat_txt.set_text(chr(10).join(lines))

        # -------------------------------------------------- notification feed
        if engine is not None:
            st = engine.stats()
            head = ["{} events -> {} notified".format(st["events"], st["notified"]),
                    "{} suppressed ({:.0%})".format(st["suppressed"],
                                                    st["suppression_rate"]),
                    "-" * 34]
        else:
            head = ["waiting for first solve", "-" * 34]
        body = []
        for n_ in notifs[-4:][::-1]:
            body.append("[{}] {}".format(n_.event.severity.upper()[:4],
                                         n_.event.kind[:26]))
            for ln in _wrap(n_.event.message, 34)[:2]:
                body.append("   " + ln)
            body.append("   imp {:.2f}  urg {}".format(n_.importance, n_.urgency))
            body.append("")
        if not body:
            body = ["", "no notifications", "",
                    "Nothing has crossed the", "importance bar. Events are",
                    "still recorded and exported."]
        notif_txt.set_text(chr(10).join(head + body))
        if notifs:
            notif_txt.set_color(SEV_COLOUR.get(notifs[-1].event.severity, "0.2"))
        else:
            notif_txt.set_color("0.45")

        # -------------------------------------------------- activity log
        try:
            tail = state.log_tail(64)
            wrapped = []
            for ts, msg in tail:
                stamp = "{:5.0f}s ".format(ts)
                parts = _wrap(msg, 44) or [""]
                wrapped.append(stamp + parts[0])
                # Continuation lines indent under the message, not the stamp.
                wrapped.extend(" " * 7 + p_ for p_ in parts[1:])
            log_txt.set_text(chr(10).join(wrapped[-58:]))
        except Exception:
            pass

        return im, lc_line, pg_line, fold_line, hist_line, big, verdict, sub

    anim = FuncAnimation(fig, update, interval=int(1000 * args.redraw_every),
                         blit=False, cache_frame_data=False)
    fig._anim = anim
    plt.show()
    state.running = False


def _wrap(text, width):
    words, line, out = str(text).split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out
