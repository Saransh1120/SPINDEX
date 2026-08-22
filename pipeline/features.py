"""Feature extraction from a photometric light curve.

Turns a raw (time, flux) series into a flat, named, JSON-serialisable feature
vector. Everything downstream -- anomaly detection, the importance rules, the
exported session record -- consumes this rather than the raw arrays, so there is
exactly one definition of every quantity and one place to fix it.

Four families:

  periodicity   What the periodogram says: dominant period, how sharp the peak
                is, how much power sits in harmonics, how ordered the spectrum
                is overall.
  morphology    What one folded rotation looks like: how many glints, how sharp,
                how asymmetric, how deep the modulation.
  photometry    Whether the measurement itself is sound: SNR, saturation,
                background level and stability.
  stability     Whether it is changing: period drift, amplitude drift, sampling
                cadence and dropouts.

Design notes
------------
Every feature is a plain float or int, never a numpy scalar, so the whole record
round-trips through JSON without custom encoders. Features that cannot be
computed return NaN rather than raising or being omitted -- a missing key is a
schema change that breaks consumers, whereas NaN is a value they can test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np

import period as ps

FEATURE_VERSION = "1.0.0"


def _f(x) -> float:
    """Coerce to a plain float, mapping non-finite to NaN."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v if math.isfinite(v) else float("nan")


def _positive_unit_mean(y: np.ndarray) -> np.ndarray:
    """Shift/scale so folding and boxcar helpers behave on any input.

    ps.flatten divides by a running mean; on a zero-mean series that divisor
    crosses zero and the result is garbage. This is the guard.
    """
    y = np.asarray(y, dtype=float)
    y = y - y.min() + 1e-12
    m = y.mean()
    return y / m if m > 0 else y


@dataclass
class FeatureSet:
    """One feature vector, with the provenance needed to interpret it."""

    version: str = FEATURE_VERSION
    n_samples: int = 0
    span_s: float = float("nan")

    # --- periodicity
    period_s: float = float("nan")
    frequency_hz: float = float("nan")
    ls_peak_period_s: float = float("nan")
    harmonic_order: float = float("nan")
    ls_power: float = float("nan")
    fap: float = float("nan")
    peak_q: float = float("nan")
    harmonic_ratio: float = float("nan")
    spectral_entropy: float = float("nan")
    n_significant_peaks: int = 0

    # --- morphology
    n_glints: int = 0
    modulation_depth: float = float("nan")
    amplitude: float = float("nan")
    duty_cycle: float = float("nan")
    glint_sharpness: float = float("nan")
    profile_skew: float = float("nan")
    profile_asymmetry: float = float("nan")
    fold_scatter: float = float("nan")

    # --- photometry
    mean_flux: float = float("nan")
    flux_std: float = float("nan")
    snr: float = float("nan")
    saturated_fraction: float = float("nan")
    background_level: float = float("nan")
    background_drift: float = float("nan")
    baseline_slope_per_s: float = float("nan")

    # --- stability
    period_drift_s_per_s: float = float("nan")
    amplitude_drift_per_s: float = float("nan")
    cadence_hz: float = float("nan")
    cadence_jitter_ms: float = float("nan")
    dropout_fraction: float = float("nan")

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_lines(self) -> list[str]:
        return [
            "period        {:>12.5f} s   ({:.4f} Hz, harmonic x{:g})".format(
                self.period_s, self.frequency_hz, self.harmonic_order),
            "LS power      {:>12.4f}     FAP {:.2e}   Q {:.1f}".format(
                self.ls_power, self.fap, self.peak_q),
            "glints/rev    {:>12d}       depth {:.3f}   duty {:.3f}".format(
                self.n_glints, self.modulation_depth, self.duty_cycle),
            "SNR           {:>12.1f}       sat {:.2%}".format(
                self.snr, self.saturated_fraction),
            "cadence       {:>12.2f} Hz    jitter {:.2f} ms".format(
                self.cadence_hz, self.cadence_jitter_ms),
        ]


# ------------------------------------------------------------------ periodicity


def _peak_quality(freq, power, k):
    """Q = f / FWHM of the periodogram peak. Higher means a better-defined rate.

    A coherent rotator gives a narrow peak whose width is set by the baseline.
    A wandering or intermittent one gives a broad smear at the same frequency.
    Both have the same peak *position*, so period alone cannot tell them apart --
    Q can, which is why it is worth the few lines.
    """
    if not (0 < k < power.size - 1):
        return float("nan")
    half = power[k] / 2.0
    lo = k
    while lo > 0 and power[lo] > half:
        lo -= 1
    hi = k
    while hi < power.size - 1 and power[hi] > half:
        hi += 1
    fwhm = float(freq[hi] - freq[lo])
    return _f(freq[k] / fwhm) if fwhm > 0 else float("nan")


def _spectral_entropy(power):
    """Shannon entropy of the normalised power spectrum, in [0, 1].

    Near 0 = all power in one place (a clean rotator). Near 1 = flat, i.e. noise
    with no preferred frequency. This is the single best scalar for "is there a
    signal at all", and unlike FAP it does not assume a noise model.
    """
    p = np.asarray(power, dtype=float)
    p = p[np.isfinite(p) & (p > 0)]
    if p.size < 4:
        return float("nan")
    p = p / p.sum()
    h = -np.sum(p * np.log(p))
    return _f(h / math.log(p.size))


def extract_periodicity(t, y, fmin, fmax, f_split=0.0, solve=None) -> dict:
    """Periodogram-derived features. ``solve`` may carry a precomputed solution."""
    out = {}
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    span = float(t[-1] - t[0]) if t.size > 1 else float("nan")

    if solve is not None and getattr(solve, "freq", None) is not None:
        freq, power = solve.freq, solve.spec
        p_adopted = solve.p_adopted
        p_peak = solve.p_peak
        f_peak = solve.f_peak
        out["ls_power"] = _f(solve.power)
        out["fap"] = _f(solve.fap)
    else:
        yy = y - y.mean()
        sd = yy.std()
        if sd > 0:
            yy = yy / sd
        freq, power = ps.gls(t - t[0], yy, None, f_min=fmin, f_max=fmax,
                             oversample=8.0)
        k = int(np.argmax(power))
        f_peak = _f(ps.refine_peak(t - t[0], yy, None, float(freq[k])))
        p_peak = 1.0 / f_peak if f_peak > 0 else float("nan")
        out["ls_power"] = _f(power[k])
        out["fap"] = float("nan")

        # Resolve the harmonic here too. Without this the standalone path
        # returns the raw periodogram peak -- P/2 or P/4 for a symmetric target
        # -- while the live path returns the true period, so the same curve
        # would yield two different "period_s" depending on who asked. The walk
        # needs positive unit-mean input and an evenly spaced grid; see
        # ps.flatten for why.
        p_adopted = p_peak
        if math.isfinite(p_peak) and p_peak > 0 and span > 6.0 * p_peak:
            tt = t - t[0]
            dt = float(np.median(np.diff(tt)))
            if dt > 0:
                grid = np.arange(0.0, tt[-1], dt)
                yg = _positive_unit_mean(np.interp(grid, tt, y))
                try:
                    p_adopted, _v, _tr = ps.walk_subharmonics(
                        grid, yg, None, f_peak, max_doublings=3,
                        max_period=span / 8.0)
                except Exception:
                    p_adopted = p_peak

    out["period_s"] = _f(p_adopted)
    out["frequency_hz"] = _f(1.0 / p_adopted) if p_adopted and p_adopted > 0 else float("nan")
    out["ls_peak_period_s"] = _f(p_peak)
    out["harmonic_order"] = _f(p_adopted / p_peak) if p_peak and p_peak > 0 else float("nan")

    if freq is not None and power is not None and freq.size > 4:
        k = int(np.argmax(power))
        out["peak_q"] = _peak_quality(freq, power, k)
        out["spectral_entropy"] = _spectral_entropy(power)

        # Power at twice the adopted frequency, relative to the fundamental.
        # A symmetric two-glint target puts most of its power at 2f; a
        # single-glint one does not. This distinguishes shape, not rate.
        f0 = out["frequency_hz"]
        if math.isfinite(f0) and f0 > 0:
            def _power_at(fq):
                if fq < freq[0] or fq > freq[-1]:
                    return float("nan")
                return _f(np.interp(fq, freq, power))
            p1, p2 = _power_at(f0), _power_at(2.0 * f0)
            out["harmonic_ratio"] = _f(p2 / p1) if p1 and p1 > 0 else float("nan")

        thresh = 0.25 * float(np.max(power))
        peaks = ps.find_peaks(freq, power, n=12, baseline=span)
        out["n_significant_peaks"] = int(sum(1 for _, pw in peaks if pw >= thresh))

    return out


# ------------------------------------------------------------------ morphology


def extract_morphology(t, y, period, nbins=64) -> dict:
    """Shape of one folded rotation.

    These are the features that distinguish *targets*, not rates. Two objects
    can share a period and look nothing alike: one clean glint per turn versus
    two, a sharp specular spike versus a broad diffuse hump. That is the
    difference between Model A and Model B in the build spec, and it is what a
    light curve carries beyond timing.
    """
    out = {}
    if not (period and period > 0):
        return out
    t = np.asarray(t, float)
    y = np.asarray(y, float)

    # Profile shape needs several rotations to average over. Folding two cycles
    # produces a profile dominated by whatever the noise did on those two
    # passes: the glint count then jitters between 2 and 3 on an unchanging
    # target, and the drift detector faithfully reports that jitter as a
    # morphology change. Returning nothing is the honest answer -- a missing
    # feature is visible, whereas a fabricated one is not.
    span = float(t[-1] - t[0]) if t.size > 1 else 0.0
    min_cycles = 4.0
    if span < min_cycles * period:
        return out

    if t.size < 3 * nbins:
        nbins = max(int(t.size / 3), 8)

    tt = t - t[0]
    yp = _positive_unit_mean(y)
    prof, err = ps.fold_profile(tt, yp, None, period, nbins=nbins)
    good = np.isfinite(prof)
    if good.sum() < 8:
        return out

    p = prof[good]
    lo, hi = float(p.min()), float(p.max())
    rng = hi - lo

    out["modulation_depth"] = _f(rng / (hi + lo)) if (hi + lo) > 0 else float("nan")
    out["amplitude"] = _f(rng)
    out["fold_scatter"] = _f(np.nanmedian(err[good]))

    # Duty cycle: fraction of the cycle spent above half maximum. A narrow
    # specular glint has a small duty cycle; a broad diffuse lobe a large one.
    if rng > 0:
        halfmax = lo + 0.5 * rng
        out["duty_cycle"] = _f(np.mean(p >= halfmax))

        # Glint count: local maxima that clear 50 % of the range, on the
        # circularly-wrapped profile so a peak at phase 0 is not missed.
        #
        # The profile is smoothed over 3 bins first, and peaks must also clear a
        # prominence floor. Without both, a single noisy bin registers as a
        # glint: the count then jitters between 2 and 3 on an unchanging target,
        # and because a mostly-constant history has a tiny MAD, the drift
        # detector reads that jitter as a morphology change and notifies on it.
        # A false alarm on a healthy run is more corrosive than a missed subtle
        # one, so the count is deliberately conservative.
        ps_ = np.convolve(np.concatenate([p[-1:], p, p[:1]]),
                          np.ones(3) / 3.0, mode="same")[1:-1]
        w = np.concatenate([ps_[-1:], ps_, ps_[:1]])
        core = w[1:-1]
        prominence = 0.15 * rng
        is_peak = ((core > w[:-2]) & (core >= w[2:]) & (core >= halfmax)
                   & (core - ps_.min() >= prominence))
        out["n_glints"] = int(is_peak.sum())

    # Third and fourth moments of the profile: skew says whether brightening is
    # faster than fading (or the reverse), kurtosis how peaked it is.
    mu, sd = p.mean(), p.std()
    if sd > 0:
        out["profile_skew"] = _f(np.mean(((p - mu) / sd) ** 3))
        out["glint_sharpness"] = _f(np.mean(((p - mu) / sd) ** 4) - 3.0)

    # Asymmetry between the two half-cycles. Near zero means the target looks
    # the same after half a turn, which is exactly the P vs P/2 degeneracy.
    h = p.size // 2
    if h >= 4:
        a, b = p[:h], p[h:2 * h]
        denom = np.std(np.concatenate([a, b]))
        out["profile_asymmetry"] = _f(np.mean(np.abs(a - b)) / denom) if denom > 0 else float("nan")

    return out


# ------------------------------------------------------------------ photometry


def extract_photometry(t, y, background=None, saturated=None,
                       period=None) -> dict:
    """Whether the measurement itself is trustworthy."""
    out = {}
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    if y.size < 4:
        return out

    out["mean_flux"] = _f(y.mean())
    out["flux_std"] = _f(y.std())

    # SNR against the scatter *not* explained by the fold. Using the raw std
    # would count the rotation itself as noise and report a worse SNR the
    # stronger the signal -- backwards.
    resid_sd = float("nan")
    if period and period > 0 and y.size > 64:
        tt = t - t[0]
        yp = _positive_unit_mean(y)
        prof, _e = ps.fold_profile(tt, yp, None, period, nbins=48)
        phase = (tt / period) % 1.0
        idx = np.clip((phase * prof.size).astype(int), 0, prof.size - 1)
        model = prof[idx]
        ok = np.isfinite(model)
        if ok.sum() > 16:
            resid_sd = float(np.std(yp[ok] - model[ok]))
            out["snr"] = _f((np.nanmax(prof) - np.nanmin(prof)) / resid_sd) \
                if resid_sd > 0 else float("nan")
    if not math.isfinite(out.get("snr", float("nan"))):
        sd = y.std()
        out["snr"] = _f(abs(y.mean()) / sd) if sd > 0 else float("nan")

    if saturated is not None and len(saturated):
        out["saturated_fraction"] = _f(np.mean(np.asarray(saturated, dtype=bool)))
    if background is not None and len(background) > 2:
        b = np.asarray(background, float)
        out["background_level"] = _f(np.median(b))
        out["background_drift"] = _f(b.std())

    if t.size > 8 and (t[-1] - t[0]) > 0:
        slope = np.polyfit(t - t[0], y, 1)[0]
        out["baseline_slope_per_s"] = _f(slope / (abs(y.mean()) or 1.0))
    return out


# ------------------------------------------------------------------- stability


def extract_stability(t, y, period_history=None, nseg=4) -> dict:
    """Is anything changing over the record?

    Period and amplitude drift are computed by splitting the record into
    segments and fitting a line through the per-segment values. That is cruder
    than a full time-frequency analysis and deliberately so: it needs no tuning,
    degrades gracefully on short records, and the quantity it produces is the one
    the notification rules actually test.
    """
    out = {}
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    if t.size > 2:
        dt = np.diff(t)
        dt = dt[dt > 0]
        if dt.size:
            med = float(np.median(dt))
            out["cadence_hz"] = _f(1.0 / med)
            out["cadence_jitter_ms"] = _f(1e3 * float(np.std(dt)))
            # A gap over 3x the median cadence is a dropped frame, not jitter.
            out["dropout_fraction"] = _f(np.mean(dt > 3.0 * med))

    if period_history is not None and len(period_history) >= 4:
        ph = np.asarray(period_history, float)
        if ph.ndim == 2 and ph.shape[1] == 2:
            ht, hp = ph[:, 0], ph[:, 1]
            ok = np.isfinite(hp)
            if ok.sum() >= 4 and (ht[ok].max() - ht[ok].min()) > 0:
                out["period_drift_s_per_s"] = _f(np.polyfit(ht[ok], hp[ok], 1)[0])

    if t.size > 8 * nseg:
        edges = np.linspace(t[0], t[-1], nseg + 1)
        centres, amps = [], []
        for i in range(nseg):
            m = (t >= edges[i]) & (t < edges[i + 1])
            if m.sum() > 8:
                seg = y[m]
                centres.append(0.5 * (edges[i] + edges[i + 1]))
                amps.append(float(np.percentile(seg, 95) - np.percentile(seg, 5)))
        if len(amps) >= 3:
            a = np.array(amps)
            base = abs(a.mean()) or 1.0
            out["amplitude_drift_per_s"] = _f(np.polyfit(centres, a, 1)[0] / base)
    return out


# ------------------------------------------------------------------ orchestration


def extract(t, y, fmin=0.01, fmax=5.0, background=None, saturated=None,
            period_history=None, solve=None, f_split=0.0) -> FeatureSet:
    """Full feature vector for one light curve.

    ``solve`` accepts a live.Solution so the periodogram is not recomputed when
    the caller already has one -- the live path solves once per second and would
    otherwise pay for it twice.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    fs = FeatureSet(n_samples=int(t.size),
                    span_s=_f(t[-1] - t[0]) if t.size > 1 else float("nan"))
    if t.size < 16:
        return fs

    d = {}
    d.update(extract_periodicity(t, y, fmin, fmax, f_split=f_split, solve=solve))
    d.update(extract_morphology(t, y, d.get("period_s")))
    d.update(extract_photometry(t, y, background, saturated, d.get("period_s")))
    d.update(extract_stability(t, y, period_history))

    for k, v in d.items():
        if hasattr(fs, k):
            setattr(fs, k, v)
    return fs


FEATURE_DESCRIPTIONS = {
    "period_s": "adopted rotation period after harmonic resolution",
    "frequency_hz": "1 / period_s",
    "ls_peak_period_s": "raw Lomb-Scargle peak, before the sub-harmonic walk",
    "harmonic_order": "period_s / ls_peak_period_s; 2 or 4 for symmetric targets",
    "ls_power": "normalised periodogram power at the peak, 0-1",
    "fap": "false-alarm probability of the peak",
    "peak_q": "peak frequency / FWHM; how well-defined the rate is",
    "harmonic_ratio": "power at 2f relative to f; shape indicator",
    "spectral_entropy": "0 = single clean tone, 1 = featureless noise",
    "n_significant_peaks": "peaks above 25 % of the maximum",
    "n_glints": "maxima per folded rotation above half range",
    "modulation_depth": "(max-min)/(max+min) of the folded profile",
    "amplitude": "peak-to-peak of the folded profile",
    "duty_cycle": "fraction of the cycle above half maximum",
    "glint_sharpness": "excess kurtosis of the folded profile",
    "profile_skew": "skew of the folded profile",
    "profile_asymmetry": "difference between the two half-cycles; ~0 means P/2 degenerate",
    "fold_scatter": "median standard error of the folded profile bins",
    "mean_flux": "mean aperture flux",
    "flux_std": "standard deviation of the flux",
    "snr": "fold amplitude divided by residual scatter",
    "saturated_fraction": "fraction of frames with a clipped pixel in the aperture",
    "background_level": "median annulus background per pixel",
    "background_drift": "standard deviation of the background over the record",
    "baseline_slope_per_s": "fractional linear trend per second",
    "period_drift_s_per_s": "slope of recovered period against time",
    "amplitude_drift_per_s": "fractional amplitude change per second",
    "cadence_hz": "median sampling rate from real timestamps",
    "cadence_jitter_ms": "standard deviation of the sample interval",
    "dropout_fraction": "fraction of intervals over 3x the median",
}
