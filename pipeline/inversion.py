"""Stages 7 and 8: fit the forward model to a measured light curve.

scipy.optimize.least_squares gives the point estimate, emcee gives posteriors.
Reported as medians with 16th/84th percentiles, per Rev B section 10.2 -- a
recovered period quoted without an interval is an assertion, not a measurement.

Four parameters are physical and two are geometric nuisances:

    p_spin       Axis A period                         [s]
    p_prec       Axis B period                         [s]
    cone_deg     half-angle of the spin-axis sweep     [deg]
    area_ratio   one-sided panel area / total bus area [-]
    spin_phase0  spin phase at t = 0                   [rad]
    prec_phase0  rock phase at t = 0                   [cycles]

Brightness scale and additive offset are *not* sampled. They enter the model
linearly, so at every likelihood evaluation they are solved for exactly by
linear least squares and profiled out. This drops the sampled dimension from
eight to six, removes the two most strongly degenerate directions from the
posterior, and costs one 2x2 solve per evaluation. Absolute panel area is
degenerate with lamp brightness and distance and is not recoverable at all
(Rev B section 3.2), which is why the fitted quantity is a ratio.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares

import forward_model as fm

PARAM_NAMES = ("p_spin", "p_prec", "cone_deg", "area_ratio",
               "spin_phase0", "prec_phase0")
PARAM_UNITS = ("s", "s", "deg", "-", "rad", "cyc")

# The model is exactly periodic in these two, so parameter space is a torus in
# those directions and proposals are wrapped rather than rejected at a boundary.
PHASE_PERIODS = {4: 2.0 * np.pi, 5: 1.0}


def wrap_phases(theta: np.ndarray) -> np.ndarray:
    theta = np.asarray(theta, dtype=float).copy()
    for i, per in PHASE_PERIODS.items():
        theta[i] = theta[i] % per
    return theta


def model_shape(theta, t, model: str, phase_angle_deg=None) -> np.ndarray:
    """Unscaled model flux. Brightness scale and offset are applied later."""
    p_spin, p_prec, cone_deg, area_ratio, sp0, pp0 = theta
    panel_area = fm.panel_area_for_ratio(model, max(area_ratio, 1e-6))
    params = fm.LightCurveParams(
        p_spin=p_spin, p_prec=p_prec, cone_deg=cone_deg,
        panel_area=panel_area, spin_phase0=sp0, prec_phase0=pp0,
        rho_d=1.0, scale=1.0, offset=0.0,
    )
    facets = fm.MODELS[model](panel_area=panel_area)
    kw = {} if phase_angle_deg is None else {"phase_angle_deg": phase_angle_deg}
    return fm.light_curve(t, params, facets, **kw)


def profile_linear(m: np.ndarray, y: np.ndarray, w: np.ndarray):
    """Best-fit (scale, offset) for y ~ scale*m + offset, weighted by w.

    Exact, not iterated: the two linear parameters are solved at every model
    evaluation so the sampler never has to explore them.
    """
    A = np.column_stack([m * w, w])
    coef, *_ = np.linalg.lstsq(A, y * w, rcond=None)
    return float(coef[0]), float(coef[1])


def predict(theta, t, y, dy, model: str, phase_angle_deg=None):
    """Model evaluated with the linear parameters profiled out."""
    m = model_shape(theta, t, model, phase_angle_deg)
    w = 1.0 / np.asarray(dy, dtype=float)
    a, b = profile_linear(m, y, w)
    return a * m + b, a, b


def residuals(theta, t, y, dy, model: str, phase_angle_deg=None) -> np.ndarray:
    fit, _, _ = predict(theta, t, y, dy, model, phase_angle_deg)
    return (y - fit) / dy


# ------------------------------------------------------------------- bounds


def default_bounds(guess, model: str):
    """Bounds for least_squares and the uniform prior for emcee.

    The period bounds are deliberately tight -- a few parts in a thousand around
    the period-search value. Over a 120 s recording the spin phase advances
    through 65 cycles, so the chi2 surface in p_spin is a comb of narrow minima
    one cycle-slip apart. Opening the bounds does not find a better optimum, it
    finds a different alias. The period search has already located the right
    tooth of the comb to about 1e-5; the fit's job is to polish it, not to
    re-search.
    """
    p_spin, p_prec = guess[0], guess[1]
    lo = np.array([p_spin * 0.997, p_prec * 0.95, 2.0, 0.005,
                   -4.0 * np.pi, -2.0])
    hi = np.array([p_spin * 1.003, p_prec * 1.05, 89.0, 3.0,
                   4.0 * np.pi, 2.0])
    return lo, hi


@dataclass
class FitResult:
    theta: np.ndarray
    scale: float
    offset: float
    chi2: float
    dof: int
    chi2_red: float
    success: bool
    message: str
    dy_scaled: np.ndarray

    def as_dict(self):
        return {n: float(v) for n, v in zip(PARAM_NAMES, self.theta)}


def fit_least_squares(t, y, dy, guess, model: str, phase_angle_deg=None,
                      rescale_errors=True) -> FitResult:
    """Point estimate by Levenberg-Marquardt-style trust-region least squares.

    If ``rescale_errors``, the photometric errors are multiplied by
    sqrt(reduced chi2) after the fit. The forward model is a parameterised
    box-wing, not the truth -- it has no self-shadowing and no secondary
    scattering -- so residuals carry model error as well as photon noise.
    Feeding the raw errors into emcee would produce posteriors tighter than the
    model deserves and intervals that do not cover the truth. Rescaling states
    the assumption that the model is right on average and lets the data set the
    scatter.

    Rescaling is a correction for model error, not a licence to ignore a bad
    chi2. A factor near 1.2 is model error; a factor of 13 means the error bars
    themselves are wrong. That happened here once -- the photometry gain was set
    by guess rather than from the renderer's photon scale, giving chi2_red = 175
    where the correct gain gives 1.55 -- and the rescaling hid it completely.
    Look at the raw chi2_red before trusting the interval.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    dy = np.asarray(dy, float)
    lo, hi = default_bounds(guess, model)
    x0 = np.clip(np.asarray(guess, float), lo + 1e-12, hi - 1e-12)

    sol = least_squares(
        residuals, x0, bounds=(lo, hi), args=(t, y, dy, model, phase_angle_deg),
        x_scale=np.array([guess[0], guess[1], 30.0, 0.2, 1.0, 0.3]),
        xtol=1e-14, ftol=1e-14, gtol=1e-12, max_nfev=4000,
    )

    theta = wrap_phases(sol.x)
    fit, a, b = predict(theta, t, y, dy, model, phase_angle_deg)
    chi2 = float(np.sum(((y - fit) / dy) ** 2))
    dof = int(t.size - len(theta) - 2)          # +2 for the profiled linear pair
    chi2_red = chi2 / dof

    dy_out = dy * np.sqrt(chi2_red) if (rescale_errors and chi2_red > 0) else dy
    return FitResult(theta, a, b, chi2, dof, chi2_red, bool(sol.success),
                     str(sol.message), dy_out)


# ---------------------------------------------------------------------- emcee


def log_prob(theta, t, y, dy, model, lo, hi, phase_angle_deg=None):
    """Uniform priors inside the bounds, Gaussian likelihood.

    Phases are wrapped before the bounds check: the model is exactly periodic in
    them, so those directions are a torus and a walker leaving one edge should
    re-enter at the other rather than be rejected.
    """
    th = wrap_phases(theta)
    check = np.array([True] * len(th))
    for i in PHASE_PERIODS:
        check[i] = False
    if np.any(th[check] < lo[check]) or np.any(th[check] > hi[check]):
        return -np.inf
    try:
        r = residuals(th, t, y, dy, model, phase_angle_deg)
    except (ValueError, FloatingPointError):
        return -np.inf
    if not np.all(np.isfinite(r)):
        return -np.inf
    return -0.5 * float(np.sum(r * r))


@dataclass
class Posterior:
    samples: np.ndarray            # (nsteps*nwalkers, ndim) after burn-in
    median: np.ndarray
    lo16: np.ndarray
    hi84: np.ndarray
    acceptance: float
    autocorr: np.ndarray | None
    nwalkers: int
    nsteps: int
    burn: int
    thin_data: int

    def interval(self, i):
        return self.median[i], self.median[i] - self.lo16[i], self.hi84[i] - self.median[i]

    def table(self, truth=None) -> str:
        rows = ["  {:<13}{:>13}{:>12}{:>12}".format(
            "parameter", "median", "-16th", "+84th")]
        for i, (n, u) in enumerate(zip(PARAM_NAMES, PARAM_UNITS)):
            med, m, p = self.interval(i)
            rows.append("  {:<13}{:>13.5f}{:>12.5f}{:>12.5f}   {}".format(
                n, med, m, p, u))
        return "\n".join(rows)


def run_mcmc(t, y, dy, theta0, model: str, nwalkers=32, nsteps=2500, burn=None,
             thin_data=3, phase_angle_deg=None, seed=0, progress=True):
    """Sample the posterior around a least-squares solution.

    ``thin_data`` decimates the light curve for sampling only. At 60 fps the
    highest frequency of interest is about 2.2 Hz, so keeping every third sample
    still leaves Nyquist at 10 Hz -- an order of magnitude of margin -- while
    cutting the likelihood cost by three. The full-resolution curve is still
    what the least-squares point estimate and the residual plot use.
    """
    import emcee

    t = np.asarray(t, float)[::thin_data]
    y = np.asarray(y, float)[::thin_data]
    dy = np.asarray(dy, float)[::thin_data]

    lo, hi = default_bounds(theta0, model)
    ndim = len(theta0)
    burn = burn if burn is not None else nsteps // 4

    rng = np.random.default_rng(seed)
    # Ball sized to each parameter rather than a flat fraction: a 1e-3 relative
    # kick to p_spin is a phase slip of several cycles, while the same kick to
    # cone_deg is nothing.
    spread = np.array([theta0[0] * 2e-5, theta0[1] * 2e-3, 0.4, 0.01, 0.02, 0.002])
    p0 = theta0 + spread * rng.standard_normal((nwalkers, ndim))
    for i in PHASE_PERIODS:
        p0[:, i] = p0[:, i] % PHASE_PERIODS[i]
    p0 = np.clip(p0, lo + 1e-9, hi - 1e-9)

    sampler = emcee.EnsembleSampler(
        nwalkers, ndim, log_prob,
        args=(t, y, dy, model, lo, hi, phase_angle_deg))
    sampler.run_mcmc(p0, nsteps, progress=progress)

    chain = sampler.get_chain(discard=burn, flat=True)
    chain = np.array([wrap_phases(c) for c in chain])

    try:
        tau = sampler.get_autocorr_time(discard=burn, quiet=True)
    except Exception:
        tau = None

    med = np.median(chain, axis=0)
    lo16 = np.percentile(chain, 16, axis=0)
    hi84 = np.percentile(chain, 84, axis=0)

    return Posterior(chain, med, lo16, hi84,
                     float(np.mean(sampler.acceptance_fraction)), tau,
                     nwalkers, nsteps, burn, thin_data)
