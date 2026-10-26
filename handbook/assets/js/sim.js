/* ===========================================================================
   SPINDEX — simulation core
   ---------------------------------------------------------------------------
   A faithful JavaScript port of two modules in this repository:

     pipeline/forward_model.py   box-wing reflection, spin + triangular rock
     pipeline/period.py          generalised Lomb-Scargle (Zechmeister &
                                 Kuerster 2009), floating-mean normalisation

   Nothing here is decorative. The flux the 3D scene renders is the same
   quantity the Python forward model computes, and the period the HUD reports
   is recovered by the same periodogram the pipeline uses. If the two drifted
   apart the handbook would be showing something the project does not do.
   =========================================================================== */

/* ---------------------------------------------------------------- constants */

/** Commanded motion, derived in pipeline/config.py from firmware step rates. */
export const TRUTH = Object.freeze({
  fullStepsPerRev: 200,          // 1.8 deg stepper
  microstepping: 16,             // MS1 = MS2 high on the TMC2209
  ustepsPerMotorRev: 3200,
  axisBRatio: 3.0,               // GT2 60T driven by 20T
  rateA: 1730.0,                 // microsteps/s
  rateB: 561.0,                  // microsteps/s
  sweepAmp: 1600.0,              // microsteps from centre
  pSpin: 3200 / 1730,            // 1.8497109826589595 s
  pPrec: (4 * 1600) / 561,       // 11.408199643493761 s
  coneDeg: 60.0,                 // 1600 * 0.0375 deg/ustep
  phaseDeg: 30.0,                // lamp-to-target-to-lens, Rev B 8.1
  fps: 60.0,
});

/* ------------------------------------------------------------------ facets */

const BUS = { rhoD: 0.35, rhoS: 0.25, shine: 8.0 };        // creased mylar
const PANEL_FRONT = { rhoD: 0.06, rhoS: 2.6, shine: 180.0 }; // acrylic facing
const PANEL_BACK = { rhoD: 0.22, rhoS: 0.0, shine: 1.0 };    // bare PLA

function facet(normal, area, kw, label, isPanel = false) {
  const n = Math.hypot(normal[0], normal[1], normal[2]);
  return {
    normal: [normal[0] / n, normal[1] / n, normal[2] / n],
    area, label, isPanel,
    rhoD: kw.rhoD, rhoS: kw.rhoS, shine: kw.shine,
  };
}

/* Bus dimensions in mm. w and d must differ: a square cross-section is
   invariant under 90 deg about the spin axis, which moves dominant periodogram
   power to 4/P_A and turns a two-way harmonic ambiguity into a four-way one. */
function busFacets(w = 60, d = 85, h = 110) {
  return [
    facet([+1, 0, 0], d * h, BUS, 'bus+x'),
    facet([-1, 0, 0], d * h, BUS, 'bus-x'),
    facet([0, +1, 0], w * h, BUS, 'bus+y'),
    facet([0, -1, 0], w * h, BUS, 'bus-y'),
    facet([0, 0, +1], w * d, BUS, 'bus+z'),
    facet([0, 0, -1], w * d, BUS, 'bus-z'),
  ];
}

/** Model A — symmetric box-wing. Invariant under 180 deg about the spin axis,
 *  so the curve is exactly periodic at P_A/2 and no photometry can tell P_A
 *  from P_A/2. That is physics, not a defect in the analysis. */
export function modelAFacets(panelArea = 4000) {
  return busFacets().concat([
    facet([0, +1, 0], panelArea, PANEL_FRONT, 'panelA_front', true),
    facet([0, -1, 0], panelArea, PANEL_BACK, 'panelA_back', true),
    facet([0, -1, 0], panelArea, PANEL_FRONT, 'panelB_front', true),
    facet([0, +1, 0], panelArea, PANEL_BACK, 'panelB_back', true),
  ]);
}

/** Model B — one panel plus an offset dish, breaking the 180 deg symmetry so
 *  consecutive half-cycles differ and the fold test can resolve the harmonic. */
export function modelBFacets(panelArea = 4000) {
  return busFacets().concat([
    facet([0, +1, 0], panelArea, PANEL_FRONT, 'panel_front', true),
    facet([0, -1, 0], panelArea, PANEL_BACK, 'panel_back', true),
    facet([0.62, 0.55, 0.56], 0.55 * panelArea,
      { rhoD: 0.30, rhoS: 0.9, shine: 30.0 }, 'dish'),
  ]);
}

export const MODELS = { A: modelAFacets, B: modelBFacets };

/* -------------------------------------------------------------- kinematics */

/** Unit triangle wave on [-1, 1], period 1, zero and rising at x = 0 — matching
 *  the firmware, which starts at sweep centre and travels positive first. */
export function triangleWave(x) {
  let u = (x - 0.25) % 1.0;
  if (u < 0) u += 1.0;
  return 4.0 * Math.abs(u - 0.5) - 1.0;
}

/** Body-to-lab rotation of one normal at spin phase phi and rock angle eps.
 *
 *      R(t) = Rx(eps) . M0 . Rz(phi),     M0 = Rx(-90 deg)
 *
 *  Body +z is the spin axis. Lab +z is the viewing direction. At zero rock the
 *  spin axis lies along lab +y, broadside to the observer, where the spin
 *  modulation is deepest. */
export function bodyToLab(n, phi, eps, out = [0, 0, 0]) {
  const c = Math.cos(phi), s = Math.sin(phi);
  const ux = n[0] * c - n[1] * s;
  const uy = n[0] * s + n[1] * c;
  const uz = n[2];
  // M0: (x, y, z) -> (x, z, -y)
  const vx = ux, vy = uz, vz = -uy;
  const ce = Math.cos(eps), se = Math.sin(eps);
  out[0] = vx;
  out[1] = vy * ce - vz * se;
  out[2] = vy * se + vz * ce;
  return out;
}

/** Unit vectors to camera and to lamp, separated by the phase angle. */
export function viewingGeometry(phaseDeg = TRUTH.phaseDeg) {
  const a = (phaseDeg * Math.PI) / 180;
  return { o: [0, 0, 1], s: [Math.sin(a), 0, Math.cos(a)] };
}

/* ------------------------------------------------------------- light curve */

/**
 * Noise-free reflected flux of a faceted target at one instant.
 *
 * Each facet contributes a Lambertian term and a Blinn-Phong specular lobe,
 * both gated on the facet being simultaneously lit and visible. The target is
 * unresolved, so only facet normals and areas matter — facet positions, and
 * therefore self-shadowing, are deliberately not modelled.
 */
export function fluxAt(t, params, facets, phaseDeg = TRUTH.phaseDeg) {
  const { o, s } = viewingGeometry(phaseDeg);
  let hx = s[0] + o[0], hy = s[1] + o[1], hz = s[2] + o[2];
  const hn = Math.hypot(hx, hy, hz);
  hx /= hn; hy /= hn; hz /= hn;

  const phi = (2 * Math.PI * t) / params.pSpin + (params.spinPhase0 ?? 0);
  const eps = ((params.coneDeg * Math.PI) / 180) *
    triangleWave(t / params.pPrec + (params.precPhase0 ?? 0));

  const areaScale = (params.panelArea ?? 4000) / 4000;
  const rhoDGlobal = params.rhoD ?? 1.0;

  let diffuse = 0, specular = 0;
  const v = [0, 0, 0];
  for (let i = 0; i < facets.length; i++) {
    const f = facets[i];
    bodyToLab(f.normal, phi, eps, v);
    const muI = v[0] * s[0] + v[1] * s[1] + v[2] * s[2];
    const muE = v[0] * o[0] + v[1] * o[1] + v[2] * o[2];
    if (muI <= 0 || muE <= 0) continue;           // not both lit and visible
    const area = f.isPanel ? f.area * areaScale : f.area;
    diffuse += ((area * f.rhoD * rhoDGlobal) / Math.PI) * muI * muE;
    if (f.rhoS > 0) {
      const nh = v[0] * hx + v[1] * hy + v[2] * hz;
      if (nh > 0) specular += area * f.rhoS * Math.pow(nh, f.shine) * muI;
    }
  }
  const scale = params.scale ?? 1.0;
  return {
    total: scale * (diffuse + specular) + (params.offset ?? 0),
    diffuse: scale * diffuse,
    specular: scale * specular,
    phi, eps,
  };
}

/** Sampled clean curve, for pre-rendering a reference trace. */
export function lightCurve(times, params, facets, phaseDeg = TRUTH.phaseDeg) {
  const out = new Float64Array(times.length);
  for (let i = 0; i < times.length; i++) {
    out[i] = fluxAt(times[i], params, facets, phaseDeg).total;
  }
  return out;
}

/* --------------------------------------------------------------- noise ---- */

/** Box-Muller, with a deterministic 32-bit LCG so a run can be reproduced. */
export class Rng {
  constructor(seed = 1) { this.s = (seed >>> 0) || 1; this.spare = null; }
  next() { this.s = (this.s * 1664525 + 1013904223) >>> 0; return this.s / 4294967296; }
  normal() {
    if (this.spare !== null) { const v = this.spare; this.spare = null; return v; }
    let u = 0, v = 0, r = 0;
    do {
      u = this.next() * 2 - 1; v = this.next() * 2 - 1; r = u * u + v * v;
    } while (r === 0 || r >= 1);
    const f = Math.sqrt((-2 * Math.log(r)) / r);
    this.spare = v * f;
    return u * f;
  }
}

/* ------------------------------------------- generalised Lomb-Scargle ------ */

/**
 * Floating-mean Lomb-Scargle power on a regular frequency grid, power
 * normalised to [0, 1]. Direct port of `gls` in pipeline/period.py.
 *
 * Lomb-Scargle rather than an FFT because the live path is unevenly sampled:
 * a webcam drops frames and USB scheduling jitters arrival times, so frame
 * index is not a clock. Every sample carries its own timestamp.
 *
 * The work is sliced across animation frames so a solve never blocks the
 * render loop. Call `step(budgetMs)` until it returns true.
 */
export class GlsSolver {
  /**
   * `fPick` splits the spectrum. Peaks above it are candidate spin (Axis A)
   * frequencies, peaks below it candidate envelope (Axis B) frequencies. The
   * two bands are separated by about a factor of six by design, so the split
   * is not delicate — but it is essential: the envelope routinely carries more
   * power than the spin, and taking the global maximum returns the precession
   * harmonic with total confidence.
   */
  constructor({ fMin = 0.05, fMax = 2.6, oversample = 8, fPick = 0.5 } = {}) {
    this.fMin = fMin; this.fMax = fMax; this.oversample = oversample;
    this.fPick = fPick;
    this.freq = null; this.power = null; this.done = true;
  }

  /** Begin a solve over (t, y). Copies its inputs; the caller may keep writing. */
  begin(t, y) {
    const n = t.length;
    if (n < 32) { this.done = true; this.result = null; return false; }
    this.t = Float64Array.from(t);
    this.y = Float64Array.from(y);
    const baseline = this.t[n - 1] - this.t[0];
    if (!(baseline > 0)) { this.done = true; this.result = null; return false; }
    this.baseline = baseline;

    const df = 1.0 / (this.oversample * baseline);
    const nf = Math.max(8, Math.floor((this.fMax - this.fMin) / df) + 1);
    this.df = df;
    this.freq = new Float64Array(nf);
    for (let i = 0; i < nf; i++) this.freq[i] = this.fMin + i * df;
    this.power = new Float64Array(nf);

    // Uniform weights; the live path has no per-sample error model.
    const w = 1 / n;
    let Y = 0, YY = 0;
    for (let i = 0; i < n; i++) { Y += w * this.y[i]; }
    for (let i = 0; i < n; i++) { YY += w * this.y[i] * this.y[i]; }
    YY -= Y * Y;
    this.w = w; this.Y = Y; this.YY = YY <= 0 ? 1e-300 : YY;
    this.k = 0; this.done = false;
    return true;
  }

  /** Advance the solve for at most `budgetMs` milliseconds. True when complete. */
  step(budgetMs = 4) {
    if (this.done) return true;
    const t = this.t, y = this.y, n = t.length;
    const w = this.w, Y = this.Y, YY = this.YY;
    const t0 = performance.now();

    while (this.k < this.freq.length) {
      const f = this.freq[this.k];
      const wt = 2 * Math.PI * f;
      let C = 0, S = 0, YC = 0, YS = 0, CC = 0, SS = 0, CS = 0;
      for (let i = 0; i < n; i++) {
        const a = wt * t[i];
        const c = Math.cos(a), s = Math.sin(a);
        C += w * c; S += w * s;
        YC += w * y[i] * c; YS += w * y[i] * s;
        CC += w * c * c; SS += w * s * s; CS += w * c * s;
      }
      YC -= Y * C; YS -= Y * S;
      CC -= C * C; SS -= S * S; CS -= C * S;
      const D = CC * SS - CS * CS;
      let p = 0;
      if (D !== 0) {
        p = (SS * YC * YC + CC * YS * YS - 2 * CS * YC * YS) / (YY * D);
      }
      this.power[this.k] = Number.isFinite(p) ? Math.min(Math.max(p, 0), 1) : 0;
      this.k++;
      if ((this.k & 7) === 0 && performance.now() - t0 > budgetMs) return false;
    }

    this.done = true;
    this.result = this.#reduce();
    return true;
  }

  /** Peak, parabolic refinement, false-alarm probability, peak quality Q. */
  #reduce() {
    const f = this.freq, p = this.power, nf = f.length;
    let k = -1;
    for (let i = 0; i < nf; i++) {
      if (f[i] < this.fPick) continue;                 // envelope band, not spin
      if (k < 0 || p[i] > p[k]) k = i;
    }
    if (k < 0) { k = 0; for (let i = 1; i < nf; i++) if (p[i] > p[k]) k = i; }

    // Parabolic vertex: a peak is ~1/T wide, so interpolation beats a finer grid.
    let fPeak = f[k];
    if (k > 0 && k < nf - 1) {
      const d = p[k - 1] - 2 * p[k] + p[k + 1];
      if (d !== 0) fPeak = f[k] + 0.5 * ((p[k - 1] - p[k + 1]) / d) * this.df;
    }

    // Baluev-style FAP for the floating-mean periodogram. Independent-frequency
    // count from the baseline, which is the usual conservative approximation.
    const n = this.t.length;
    const z = p[k];
    const m = Math.max(1, this.baseline * (this.fMax - this.fMin));
    const single = Math.pow(1 - z, (n - 3) / 2);
    const fap = Math.min(1, m * single);

    // Q = f / FWHM. Higher means a better-defined rate.
    const half = z / 2;
    let lo = k, hi = k;
    while (lo > 0 && p[lo] > half) lo--;
    while (hi < nf - 1 && p[hi] > half) hi++;
    const fwhm = Math.max(f[hi] - f[lo], this.df);

    return {
      freq: fPeak,
      period: 1 / fPeak,
      power: z,
      fap,
      q: fPeak / fwhm,
      significant: fap < 1e-3,
      kPeak: k,
    };
  }
}

/* --------------------------------------------- envelope and flattening ---- */

/** Uniform boxcar with reflected edges. Port of `moving_average`. */
export function movingAverage(y, npts) {
  let n = Math.max(1, Math.round(npts));
  if (n <= 1) return Float64Array.from(y);
  if (n % 2 === 0) n += 1;
  const pad = (n - 1) / 2;
  const out = new Float64Array(y.length);
  for (let i = 0; i < y.length; i++) {
    let s = 0;
    for (let j = -pad; j <= pad; j++) {
      let k = i + j;
      if (k < 0) k = -k;                                  // reflect
      if (k >= y.length) k = 2 * y.length - k - 2;
      s += y[Math.max(0, Math.min(y.length - 1, k))];
    }
    out[i] = s / n;
  }
  return out;
}

/**
 * Divide out variation slower than `windowS`. Port of `flatten`.
 *
 * The fold test asks whether consecutive cycles have the same *shape*. Left
 * alone it answers "no" for any target, because the precession envelope makes
 * every cycle a different height, and the walk then doubles the period
 * forever. Normalising by a boxcar one fold-window wide removes the envelope
 * and leaves the spin-locked structure, which is the thing being tested.
 */
export function flatten(t, y, windowS) {
  const dt = (t[t.length - 1] - t[0]) / Math.max(1, t.length - 1);
  const base = movingAverage(y, windowS / dt);
  const out = new Float64Array(y.length);
  for (let i = 0; i < y.length; i++) {
    out[i] = Math.abs(base[i]) > 1e-12 ? y[i] / base[i] : y[i];
  }
  return out;
}

/**
 * Direct (unsliced) periodogram for a narrow band — used for the envelope,
 * where the grid is ~100 bins and the cost is negligible.
 */
export function glsDirect(t, y, fMin, fMax, oversample = 8) {
  const n = t.length;
  const baseline = t[n - 1] - t[0];
  if (!(baseline > 0) || n < 16) return null;
  const df = 1 / (oversample * baseline);
  const nf = Math.max(4, Math.floor((fMax - fMin) / df) + 1);
  const w = 1 / n;
  let Y = 0, YY = 0;
  for (let i = 0; i < n; i++) Y += w * y[i];
  for (let i = 0; i < n; i++) YY += w * y[i] * y[i];
  YY -= Y * Y;
  if (!(YY > 0)) return null;

  const freq = new Float64Array(nf), power = new Float64Array(nf);
  for (let j = 0; j < nf; j++) {
    const f = fMin + j * df;
    freq[j] = f;
    const wt = 2 * Math.PI * f;
    let C = 0, S = 0, YC = 0, YS = 0, CC = 0, SS = 0, CS = 0;
    for (let i = 0; i < n; i++) {
      const a = wt * t[i], c = Math.cos(a), s = Math.sin(a);
      C += w * c; S += w * s;
      YC += w * y[i] * c; YS += w * y[i] * s;
      CC += w * c * c; SS += w * s * s; CS += w * c * s;
    }
    YC -= Y * C; YS -= Y * S; CC -= C * C; SS -= S * S; CS -= C * S;
    const D = CC * SS - CS * CS;
    const p = D !== 0 ? (SS * YC * YC + CC * YS * YS - 2 * CS * YC * YS) / (YY * D) : 0;
    power[j] = Number.isFinite(p) ? Math.min(Math.max(p, 0), 1) : 0;
  }
  let k = 0;
  for (let j = 1; j < nf; j++) if (power[j] > power[k]) k = j;
  let fPeak = freq[k];
  if (k > 0 && k < nf - 1) {
    const d = power[k - 1] - 2 * power[k] + power[k + 1];
    if (d !== 0) fPeak = freq[k] + 0.5 * ((power[k - 1] - power[k + 1]) / d) * df;
  }
  return { freq, power, fPeak, period: 1 / fPeak, peakPower: power[k] };
}

/* --------------------------------------------------- harmonic fold walk ---- */

/**
 * Fold the curve at P and at 2P and ask whether the two halves differ.
 *
 * Three things this has to get right, each found by building it:
 *
 *  1. One test is not enough. With realistic bus proportions the dominant peak
 *     lands at 4/P, not 2/P, so the walk has to climb until doubling stops
 *     paying.
 *  2. The envelope contaminates the test. The spin axis is also rocking, so
 *     consecutive rotations differ in height and a naive comparison says
 *     "these differ" forever. The curve is flattened first.
 *  3. The null must be empirical. Samples in one phase bin come from many
 *     rotations at many rock angles and scatter for a physical reason, so the
 *     test uses the empirical standard error, floored at the photon error.
 */
export function foldTest(t, y, P, nbins = 100, corrLen = 1) {
  const n = t.length;
  if (n < 4 * nbins) return null;
  const sum = new Float64Array(2 * nbins);
  const sum2 = new Float64Array(2 * nbins);
  const cnt = new Int32Array(2 * nbins);
  const t0 = t[0];
  for (let i = 0; i < n; i++) {
    let ph = ((t[i] - t0) / (2 * P)) % 1.0;
    if (ph < 0) ph += 1;
    const b = Math.min(2 * nbins - 1, Math.floor(ph * 2 * nbins));
    sum[b] += y[i]; sum2[b] += y[i] * y[i]; cnt[b]++;
  }
  let chi2 = 0, dof = 0;
  for (let b = 0; b < nbins; b++) {
    const b2 = b + nbins;
    if (cnt[b] < 2 || cnt[b2] < 2) continue;
    // A curve that has been boxcar-averaged has correlated neighbours, so the
    // effective sample count per bin is cnt / corrLen, not cnt. Ignoring that
    // makes every pair of half-cycles look significantly different.
    const e1 = Math.max(2, cnt[b] / corrLen), e2 = Math.max(2, cnt[b2] / corrLen);
    const m1 = sum[b] / cnt[b], m2 = sum[b2] / cnt[b2];
    const v1 = Math.max(sum2[b] / cnt[b] - m1 * m1, 0) / (e1 - 1);
    const v2 = Math.max(sum2[b2] / cnt[b2] - m2 * m2, 0) / (e2 - 1);
    const se2 = v1 + v2;
    if (!(se2 > 0)) continue;
    chi2 += ((m1 - m2) * (m1 - m2)) / se2;
    dof++;
  }
  if (dof < 8) return null;
  const chi2red = chi2 / dof;
  // Doubling only pays if the halves genuinely differ.
  return { chi2red, dof, decision: chi2red > 2.0 ? 'fundamental' : 'half' };
}

/* ------------------------------------------------------------- feature set */

/** A small live subset of the 33 features in pipeline/features.py. */
export function quickFeatures(t, y, solve) {
  const n = y.length;
  if (n < 8) return null;
  let mean = 0;
  for (let i = 0; i < n; i++) mean += y[i];
  mean /= n;
  let sd = 0, lo = Infinity, hi = -Infinity;
  for (let i = 0; i < n; i++) {
    const d = y[i] - mean; sd += d * d;
    if (y[i] < lo) lo = y[i];
    if (y[i] > hi) hi = y[i];
  }
  sd = Math.sqrt(sd / Math.max(1, n - 1));

  // Noise from successive differences, not from the standard deviation. The
  // standard deviation of a deeply modulated light curve is dominated by the
  // modulation itself — the signal — so mean/sd reports a terrible SNR for a
  // perfectly clean glint train and would fire a low-SNR alarm on every solve.
  const diffs = new Float64Array(Math.max(1, n - 1));
  for (let i = 1; i < n; i++) diffs[i - 1] = Math.abs(y[i] - y[i - 1]);
  const sorted = Float64Array.from(diffs).sort();
  const medAbsDiff = sorted[Math.floor(sorted.length / 2)];
  const sigma = (1.4826 * medAbsDiff) / Math.SQRT2;

  // Cadence and jitter from the timestamps themselves — the live path cannot
  // assume frame index is a clock.
  let dsum = 0, dsum2 = 0;
  for (let i = 1; i < n; i++) { const d = t[i] - t[i - 1]; dsum += d; dsum2 += d * d; }
  const dmean = dsum / Math.max(1, n - 1);
  const dvar = Math.max(dsum2 / Math.max(1, n - 1) - dmean * dmean, 0);

  return {
    nSamples: n,
    spanS: t[n - 1] - t[0],
    meanFlux: mean,
    fluxStd: sd,
    noiseSigma: sigma,
    snr: sigma > 0 ? mean / sigma : NaN,
    modulationDepth: hi + lo > 0 ? (hi - lo) / (hi + lo) : NaN,
    amplitude: hi - lo,
    cadenceHz: dmean > 0 ? 1 / dmean : NaN,
    cadenceJitterMs: Math.sqrt(dvar) * 1000,
    periodS: solve ? solve.period : NaN,
    lsPower: solve ? solve.power : NaN,
    fap: solve ? solve.fap : NaN,
    peakQ: solve ? solve.q : NaN,
  };
}

/* ------------------------------------------- importance-gated notification */

/* Severity weight is deliberately super-linear: the gap between "high" and
   "critical" should not be crossable by confidence alone. Mirrors
   SEVERITY_WEIGHT in pipeline/importance.py. */
const SEVERITY_WEIGHT = { info: 0.10, low: 0.25, medium: 0.50, high: 0.80, critical: 1.00 };
const SEVERITY_RANK = { info: 0, low: 1, medium: 2, high: 3, critical: 4 };

export const URGENCY_BY_KIND = {
  saturation: 'immediate',
  no_modulation: 'immediate',
  period_change: 'soon',
  rate_change: 'soon',
  cadence_change: 'soon',
  frame_dropouts: 'soon',
  morphology_change: 'soon',
  amplitude_change: 'whenever',
  insignificant_period: 'whenever',
  low_snr: 'whenever',
  baseline_falling: 'whenever',
  broad_peak: 'whenever',
};

/**
 * The written-down bar, in miniature.
 *
 *   importance = severity x confidence x max(novelty, persistence) x kind_weight
 *
 * novelty and persistence combine as max, not a product: an event matters if it
 * is either new or entrenched. Multiplying suppresses both the first occurrence
 * (no streak yet) and the hundredth (no novelty left) — precisely backwards.
 */
export class NotificationEngine {
  constructor(policy = {}) {
    this.policy = Object.assign({
      name: 'bench',
      threshold: 0.45,
      minSeverity: 'low',
      alwaysNotifySeverity: 'critical',
      cooldownS: 60,
      noveltyDecay: 0.55,
      noveltyFloor: 0.15,
      persistenceScale: 6.0,
      rateLimit: 12,
      rateWindowS: 300,
      mutedKinds: [],
      kindWeights: {},
    }, policy);
    this.seen = new Map();     // kind -> times fired/observed
    this.streak = new Map();   // kind -> consecutive occurrences
    this.lastFired = new Map();
    this.recent = [];
    this.stats = { submitted: 0, fired: 0, suppressed: 0 };
  }

  novelty(kind) {
    const k = this.seen.get(kind) || 0;
    return Math.max(this.policy.noveltyFloor, Math.pow(this.policy.noveltyDecay, k));
  }

  persistence(kind) {
    const s = this.streak.get(kind) || 0;
    return 1 - Math.exp(-s / this.policy.persistenceScale);
  }

  submit(event, now) {
    const p = this.policy;
    this.stats.submitted++;
    const kind = event.kind;
    const sev = event.severity || 'low';
    const conf = event.confidence ?? 0.8;

    const novelty = this.novelty(kind);
    const persistence = this.persistence(kind);
    const kw = p.kindWeights[kind] ?? 1.0;
    const importance = SEVERITY_WEIGHT[sev] * conf * Math.max(novelty, persistence) * kw;
    const urgency = URGENCY_BY_KIND[kind] || 'whenever';

    this.seen.set(kind, (this.seen.get(kind) || 0) + 1);
    this.streak.set(kind, (this.streak.get(kind) || 0) + 1);

    // Seven ordered gates — the order is the policy.
    let fired = true, reason = '';
    const always = SEVERITY_RANK[sev] >= SEVERITY_RANK[p.alwaysNotifySeverity];

    if (p.mutedKinds.includes(kind)) { fired = false; reason = 'kind muted'; }
    else if (always) { reason = 'always-notify severity'; }
    else if (SEVERITY_RANK[sev] < SEVERITY_RANK[p.minSeverity]) {
      fired = false; reason = `below severity floor (${p.minSeverity})`;
    } else if (importance < p.threshold) {
      fired = false; reason = `importance ${importance.toFixed(3)} < ${p.threshold}`;
    } else if (now - (this.lastFired.get(kind) ?? -1e9) < p.cooldownS) {
      fired = false; reason = `cooldown ${p.cooldownS}s`;
    } else {
      this.recent = this.recent.filter((x) => now - x < p.rateWindowS);
      if (this.recent.length >= p.rateLimit) {
        fired = false; reason = `rate limit ${p.rateLimit}/${p.rateWindowS}s`;
      } else { reason = `importance ${importance.toFixed(3)} >= ${p.threshold}`; }
    }

    if (fired) {
      this.lastFired.set(kind, now);
      this.recent.push(now);
      this.stats.fired++;
    } else {
      this.stats.suppressed++;
    }

    // No event is silently dropped: suppressed events are still recorded.
    return {
      event, fired, importance, urgency, reason,
      factors: { severity: SEVERITY_WEIGHT[sev], confidence: conf, novelty, persistence, kindWeight: kw },
      at: now,
    };
  }

  /** A kind that stops recurring loses its streak, so persistence decays. */
  observeAbsent(presentKinds) {
    for (const k of this.streak.keys()) {
      if (!presentKinds.has(k)) this.streak.set(k, 0);
    }
  }
}

/* ----------------------------------------------------------------- helpers */

export function fmt(x, n = 5) {
  return Number.isFinite(x) ? x.toFixed(n) : '—';
}

export function pctErr(recovered, truth) {
  if (!Number.isFinite(recovered) || !truth) return NaN;
  return (100 * (recovered - truth)) / truth;
}
