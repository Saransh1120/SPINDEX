"""Stages 5 and 6 of the SDC-001 pipeline: period search and harmonic resolution.

The generalised Lomb-Scargle periodogram (Zechmeister & Kuerster 2009) is
implemented directly in NumPy rather than imported from astropy. The spec names
astropy.timeseries.LombScargle; the two agree to floating-point round-off for
the floating-mean normalisation used here, and keeping it local removes a heavy
dependency from a stage that is thirty lines of arithmetic. ``gls`` will use
astropy instead if ``prefer_astropy=True`` and the package is installed, which
makes the equivalence checkable rather than asserted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ------------------------------------------------------- generalised Lomb-Scargle


def gls(t: np.ndarray, y: np.ndarray, dy: np.ndarray | None = None,
        f_min: float = 0.02, f_max: float = 2.5, oversample: float = 20.0,
        prefer_astropy: bool = False, chunk: int = 2048):
    """Floating-mean Lomb-Scargle power on a regular frequency grid.

    Returns (freq, power) with power normalised to [0, 1]; 1 means the sinusoid
    explains all of the weighted variance.
    """
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    baseline = t[-1] - t[0]
    df = 1.0 / (oversample * baseline)
    freq = np.arange(f_min, f_max + df, df)

    if prefer_astropy:
        try:
            from astropy.timeseries import LombScargle
            ls = LombScargle(t, y, dy)
            return freq, ls.power(freq, normalization="standard")
        except ImportError:
            pass

    w = np.ones_like(y) if dy is None else 1.0 / np.asarray(dy, dtype=float) ** 2
    w = w / w.sum()

    Y = np.sum(w * y)
    YY = np.sum(w * y * y) - Y * Y

    power = np.empty_like(freq)
    for i in range(0, freq.size, chunk):
        f = freq[i:i + chunk][:, None]
        wt = 2.0 * np.pi * f * t[None, :]
        c, s = np.cos(wt), np.sin(wt)

        C = c @ w
        S = s @ w
        YC = (c @ (w * y)) - Y * C
        YS = (s @ (w * y)) - Y * S
        CC = (c * c) @ w - C * C
        SS = (s * s) @ w - S * S
        CS = (c * s) @ w - C * S

        D = CC * SS - CS * CS
        with np.errstate(divide="ignore", invalid="ignore"):
            p = (SS * YC ** 2 + CC * YS ** 2 - 2.0 * CS * YC * YS) / (YY * D)
        power[i:i + chunk] = np.nan_to_num(p, nan=0.0, posinf=0.0, neginf=0.0)

    return freq, np.clip(power, 0.0, 1.0)


def refine_peak(t, y, dy, f0, half_width=None, oversample=200.0):
    """Re-run the periodogram on a narrow window around f0 and parabolically
    interpolate the maximum. Returns the refined frequency.

    A peak is about 1/T wide, so an oversample of 200 puts ~200 samples across
    it and the parabolic vertex is then limited by noise, not by grid spacing.
    Cranking the grid finer instead of interpolating costs time and buys
    nothing.
    """
    baseline = t[-1] - t[0]
    half_width = half_width if half_width is not None else 3.0 / baseline
    f, p = gls(t, y, dy, f_min=max(f0 - half_width, 1e-6),
               f_max=f0 + half_width, oversample=oversample)
    k = int(np.argmax(p))
    if 0 < k < p.size - 1:
        y0, y1, y2 = p[k - 1], p[k], p[k + 1]
        denom = y0 - 2.0 * y1 + y2
        if denom != 0.0:
            return f[k] + 0.5 * (y0 - y2) / denom * (f[1] - f[0])
    return f[k]


def find_peaks(freq, power, n=6, min_separation_bins=None, baseline=None):
    """Local maxima of the periodogram, strongest first, mutually separated."""
    if min_separation_bins is None:
        df = freq[1] - freq[0]
        # One independent resolution element is 1/T wide.
        min_separation_bins = max(int(round((1.0 / (baseline or (1.0 / df))) / df)), 3)

    order = np.argsort(power)[::-1]
    picked = []
    for k in order:
        if 0 < k < power.size - 1 and not (power[k] >= power[k - 1] and power[k] >= power[k + 1]):
            continue
        if all(abs(k - j) >= min_separation_bins for j in picked):
            picked.append(k)
        if len(picked) >= n:
            break
    picked.sort(key=lambda k: -power[k])
    return [(float(freq[k]), float(power[k])) for k in picked]


def frequency_uncertainty(t, y, freq):
    """Analytic sigma_f for a sinusoid in white noise (Montgomery & O'Donoghue 1999).

        sigma_f = sqrt(6 / N) * sigma_resid / (pi * T * A)

    A and sigma_resid come from a linear least-squares sinusoid fit at ``freq``.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    N = t.size
    T = t[-1] - t[0]

    M = np.column_stack([np.cos(2 * np.pi * freq * t), np.sin(2 * np.pi * freq * t),
                         np.ones_like(t)])
    coef, *_ = np.linalg.lstsq(M, y, rcond=None)
    resid = y - M @ coef
    A = np.hypot(coef[0], coef[1])
    sigma_n = resid.std(ddof=3)
    if A <= 0:
        return np.inf
    return np.sqrt(6.0 / N) * sigma_n / (np.pi * T * A)


# ------------------------------------------------------- phase dispersion (PDM)


def pdm(t, y, periods, nbins=50):
    """Stellingwerf theta statistic. Minima mark candidate periods.

    Independent of the Lomb-Scargle result because it makes no assumption that
    the folded profile is sinusoidal -- which matters here, since a glint
    profile is anything but.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    var_total = y.var(ddof=1)
    theta = np.empty(len(periods))

    edges = np.linspace(0.0, 1.0, nbins + 1)
    for i, P in enumerate(periods):
        phase = (t / P) % 1.0
        idx = np.clip(np.digitize(phase, edges) - 1, 0, nbins - 1)
        num = 0.0
        den = 0
        for b in range(nbins):
            v = y[idx == b]
            if v.size > 1:
                num += (v.size - 1) * v.var(ddof=1)
                den += v.size - 1
        theta[i] = (num / den) / var_total if den else np.nan
    return theta


# ------------------------------------------------------------------- filtering


def moving_average(y: np.ndarray, npts: int) -> np.ndarray:
    """Uniform moving average with reflected edges, for evenly sampled data.

    The video time base is exactly uniform by construction (Rev B section 4.2),
    so a boxcar is legitimate here and is far cheaper than a spline.
    """
    npts = max(int(npts), 1)
    if npts <= 1:
        return y.copy()
    if npts % 2 == 0:
        npts += 1
    pad = npts // 2
    ypad = np.concatenate([y[pad:0:-1], y, y[-2:-pad - 2:-1]])
    kernel = np.ones(npts) / npts
    return np.convolve(ypad, kernel, mode="valid")[:y.size]


def flatten(t: np.ndarray, y: np.ndarray, window_s: float):
    """Divide out variation slower than ``window_s``.

    The fold test below asks whether consecutive cycles have the same *shape*.
    Left alone it would answer "no" for any target, because the precession
    envelope makes every cycle a different height. Normalising by a boxcar one
    fold-window wide removes the envelope and leaves the spin-locked structure,
    which is the thing actually being tested.
    """
    dt = t[1] - t[0]
    base = moving_average(y, round(window_s / dt))
    scale = np.where(np.abs(base) > 1e-12, base, 1.0)
    return y / scale, scale


# --------------------------------------------------------- harmonic resolution


@dataclass
class HarmonicVerdict:
    """Outcome of the P versus P/2 fold test."""

    p_fundamental: float      # the longer candidate, 2 x the periodogram peak
    p_half: float             # the periodogram peak period itself
    chi2_reduced: float
    n_bins_compared: int
    decision: str             # "fundamental", "half", or "ambiguous"
    note: str = ""

    @property
    def p_adopted(self) -> float:
        return self.p_half if self.decision == "half" else self.p_fundamental


def fold_profile(t, y, dy, P, nbins=100, phase0=0.0, corr_len=1.0):
    """Bin a light curve into a mean profile over one cycle of period P.

    The returned error is the *empirical* standard error of the mean in each
    bin, floored at the propagated photometric error. This matters: the samples
    landing in one phase bin come from many different rotations, taken at many
    different rock angles, so they scatter for a physical reason as well as a
    photometric one. Quoting only the photon error would declare every pair of
    half-cycles significantly different and the fold test would always say
    "fundamental" -- which is how a harmonic-resolution step silently doubles
    every period it is given.

    ``corr_len`` is the correlation length of the input in samples. The envelope
    is produced by a boxcar of that width, so neighbouring envelope samples are
    not independent and a bin holding k of them carries only k / corr_len
    independent measurements. Ignoring this inflates chi2 by roughly corr_len
    and, again, doubles every period.
    """
    phase = ((t / P) + phase0) % 1.0
    idx = np.clip((phase * nbins).astype(int), 0, nbins - 1)

    prof = np.full(nbins, np.nan)
    err = np.full(nbins, np.nan)
    for b in range(nbins):
        m = idx == b
        k = int(m.sum())
        if not k:
            continue
        prof[b] = y[m].mean()
        n_eff = max(k / max(corr_len, 1.0), 1.0)
        emp = y[m].std(ddof=1) / np.sqrt(n_eff) if k > 1 else np.nan
        phot = np.sqrt(np.sum(dy[m] ** 2)) / k if dy is not None else np.nan
        err[b] = np.nanmax([emp, phot]) if np.isfinite(emp) or np.isfinite(phot) else np.nan
    return prof, err


def resolve_harmonic(t, y, dy, f_peak, nbins=100,
                     ambiguous_below=2.0, corr_len=1.0) -> HarmonicVerdict:
    """Decide whether the true period is 1/f_peak or twice that.

    A body with 180 deg rotational symmetry glints twice per revolution, so the
    strongest periodogram peak sits at 2/P_A. Test: fold at 2/f_peak and compare
    the two consecutive half-cycles bin by bin. If the target is symmetric the
    halves agree within noise and the extra factor of two is unsupported by the
    data; if it is not, they differ and the longer period is real.

    ``chi2_reduced`` is that comparison, normalised by the propagated
    photometric errors. Values near 1 mean "the halves are the same curve".
    """
    p_half = 1.0 / f_peak
    p_full = 2.0 * p_half

    prof, err = fold_profile(t, y, dy, p_full, nbins=2 * nbins, corr_len=corr_len)
    first, second = prof[:nbins], prof[nbins:]
    e1, e2 = err[:nbins], err[nbins:]

    good = np.isfinite(first) & np.isfinite(second) & np.isfinite(e1) & np.isfinite(e2)
    if good.sum() < 10:
        return HarmonicVerdict(p_full, p_half, np.nan, int(good.sum()), "ambiguous",
                               "too few filled phase bins to compare halves")

    d = first[good] - second[good]
    s2 = e1[good] ** 2 + e2[good] ** 2
    # A bin holding identical values has zero empirical scatter, which happens
    # on quantised or saturated live data. Drop those bins rather than dividing
    # by zero and poisoning the whole statistic with a nan.
    ok = s2 > 0
    if ok.sum() < 10:
        return HarmonicVerdict(p_full, p_half, np.nan, int(ok.sum()), "ambiguous",
                               "too few phase bins with non-zero scatter")
    chi2r = float(np.sum(d[ok] * d[ok] / s2[ok]) / ok.sum())

    if not np.isfinite(chi2r):
        return HarmonicVerdict(p_full, p_half, chi2r, int(ok.sum()), "half",
                               "chi2 not finite; declining to double the period")

    if chi2r >= ambiguous_below:
        decision, note = "fundamental", (
            "consecutive half-cycles differ significantly, so the longer period "
            "carries real structure; keep climbing"
        )
    else:
        decision, note = "half", (
            "consecutive half-cycles agree within the scatter, so doubling buys "
            "nothing; the shorter period is the repeat period of the light curve"
        )

    return HarmonicVerdict(p_full, p_half, chi2r, int(ok.sum()), decision, note)


def walk_subharmonics(t, y, dy, f_peak, max_doublings=3, nbins=100,
                      ambiguous_below=2.0, corr_len=1.0, max_period=None):
    """Climb from the periodogram peak to the longest supported period.

    The strongest peak sits at some integer multiple of the true fundamental
    frequency -- 2/P_A for a two-glint target, and higher still if the bus has
    extra symmetry. Testing P against 2P once is not enough; the test has to be
    repeated until it stops paying.

    Each round flattens the curve over the current fold window first, so the
    precession envelope cannot masquerade as cycle-to-cycle difference.

    Returns (adopted_period, final_verdict, trail) where ``trail`` is the list
    of verdicts in the order they were evaluated.
    """
    P = 1.0 / f_peak
    trail = []
    verdict = None

    for _ in range(max_doublings):
        # Refuse to climb past what the recording can support. With only a few
        # cycles in the baseline the two half-profiles are built from almost
        # disjoint data and will differ for that reason alone, so the test says
        # "fundamental" and the walk doubles away indefinitely.
        if max_period is not None and 2.0 * P > max_period:
            break
        yf, scale = flatten(t, y, 2.0 * P)
        dyf = None if dy is None else dy / np.maximum(np.abs(scale), 1e-12)
        v = resolve_harmonic(t, yf, dyf, 1.0 / P, nbins=nbins,
                             ambiguous_below=ambiguous_below, corr_len=corr_len)
        trail.append(v)
        verdict = v
        if v.decision == "fundamental":
            P = 2.0 * P
        else:
            break

    return P, verdict, trail


# --------------------------------------------------------------- orchestration


@dataclass
class PeriodSearchResult:
    freq: np.ndarray = field(repr=False)
    power: np.ndarray = field(repr=False)
    peaks: list = field(default_factory=list)

    f_spin_peak: float = np.nan       # strongest peak in the spin band
    p_spin: float = np.nan            # adopted, after the sub-harmonic walk
    p_spin_err: float = np.nan
    spin_harmonic: float = np.nan     # p_spin / (1 / f_spin_peak)
    verdict: HarmonicVerdict | None = None
    trail: list = field(default_factory=list, repr=False)

    f_prec_peak: float = np.nan
    p_prec: float = np.nan
    p_prec_err: float = np.nan
    prec_harmonic: float = np.nan
    prec_verdict: HarmonicVerdict | None = None
    prec_trail: list = field(default_factory=list, repr=False)
    prec_note: str = ""

    pdm_period: float = np.nan
    pdm_theta: float = np.nan

    env_freq: np.ndarray | None = field(default=None, repr=False)
    env_power: np.ndarray | None = field(default=None, repr=False)


def search(t, y, dy=None, f_min=0.02, f_max=2.5, oversample=20.0,
           prec_f_max=0.5) -> PeriodSearchResult:
    """Stages 5 and 6 end to end.

    ``prec_f_max`` splits the spectrum: peaks above it are candidate spin
    (Axis A) frequencies, peaks below it candidate envelope (Axis B)
    frequencies. The two bands are separated by a factor of about six by
    design (Rev B section 5.2), so the split is not delicate.

    The two axes are then handled on different versions of the signal, exactly
    as Fig. 3 describes them: the spin period comes from the fine structure with
    the envelope divided out, and the precession period comes from the envelope
    with the fine structure averaged away.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    baseline = t[-1] - t[0]

    freq, power = gls(t, y, dy, f_min=f_min, f_max=f_max, oversample=oversample)
    peaks = find_peaks(freq, power, n=10, baseline=baseline)
    res = PeriodSearchResult(freq=freq, power=power, peaks=peaks)

    # ------------------------------------------------------------ Axis A
    hi = [p for p in peaks if p[0] > prec_f_max]
    if hi:
        f0 = refine_peak(t, y, dy, hi[0][0])
        res.f_spin_peak = f0
        res.p_spin, res.verdict, res.trail = walk_subharmonics(t, y, dy, f0)
        res.spin_harmonic = res.p_spin * f0

        # The peak frequency is what the data constrains; the adopted period is
        # an integer multiple of 1/f0, so its error scales by the same integer.
        sf = frequency_uncertainty(t, y, f0)
        res.p_spin_err = res.spin_harmonic * sf / f0 ** 2

    # ------------------------------- independent check on the adopted period
    # PDM makes no assumption that the folded profile is sinusoidal, which
    # matters because a glint profile is nothing like one. Searched only near
    # the adopted period -- this is a confirmation, not a second search.
    if np.isfinite(res.p_spin):
        yf, _ = flatten(t, y, 2.0 * res.p_spin)
        grid = np.linspace(0.96 * res.p_spin, 1.04 * res.p_spin, 161)
        theta = pdm(t, yf, grid, nbins=40)
        j = int(np.nanargmin(theta))
        res.pdm_period = float(grid[j])
        res.pdm_theta = float(theta[j])

    # ------------------------------------------------------------ Axis B
    # Average over one full repeat of the fine structure to leave the envelope.
    if np.isfinite(res.p_spin):
        dt = t[1] - t[0]
        m = max(round(res.p_spin / dt), 1)
        env = moving_average(y, m)
        ef, ep = gls(t, env, None, f_min=f_min, f_max=prec_f_max,
                     oversample=oversample)
        res.env_freq, res.env_power = ef, ep

        k = int(np.argmax(ep))
        f1 = refine_peak(t, env, None, ef[k])
        res.f_prec_peak = f1
        # Rev B section 5.2 asks for about ten Axis B cycles; four is the floor
        # below which the envelope period is not measurable at all.
        res.p_prec, res.prec_verdict, res.prec_trail = walk_subharmonics(
            t, env, None, f1, max_doublings=2, corr_len=m,
            max_period=baseline / 4.0
        )
        if res.p_prec * 4.0 > baseline:
            res.prec_note = (
                "baseline {:.0f} s holds only {:.1f} cycles of the adopted {:.2f} s "
                "envelope period; Rev B section 5.2 asks for about ten. Treat P_B "
                "as indicative and record longer.".format(
                    baseline, baseline / res.p_prec, res.p_prec))
        res.prec_harmonic = res.p_prec * f1
        # frequency_uncertainty assumes N independent samples. The envelope has
        # only about N / m of them after the boxcar, and sigma_f scales as
        # 1/sqrt(N), so the analytic value is optimistic by sqrt(m).
        res.p_prec_err = (res.prec_harmonic * np.sqrt(m)
                          * frequency_uncertainty(t, env, f1) / f1 ** 2)

    return res
