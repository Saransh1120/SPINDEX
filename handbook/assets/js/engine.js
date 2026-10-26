/* ===========================================================================
   SPINDEX — headless measurement engine
   ---------------------------------------------------------------------------
   Everything between "a photon arrives" and "an operator is told": sampling
   with honest timestamps, the rolling periodogram, the sub-harmonic walk, the
   envelope search, feature extraction, the anomaly detectors, the importance
   gate and the mission state machine.

   No DOM, no three.js. The bench page draws this; it does not compute it.
   =========================================================================== */

import {
  TRUTH, fluxAt, GlsSolver, foldTest, quickFeatures, flatten,
  movingAverage, glsDirect, NotificationEngine, Rng,
} from './sim.js';

/** Fixed-capacity timestamped sample buffer with a contiguous snapshot. */
export class Ring {
  constructor(cap) {
    this.cap = cap;
    this.t = new Float64Array(cap);
    this.y = new Float64Array(cap);
    this.c = new Float64Array(cap);        // noise-free flux, for the monitor
    this.n = 0; this.head = 0;
  }
  push(t, y, c = y) {
    this.t[this.head] = t; this.y[this.head] = y; this.c[this.head] = c;
    this.head = (this.head + 1) % this.cap;
    if (this.n < this.cap) this.n++;
  }
  get length() { return this.n; }
  snapshot() {
    const t = new Float64Array(this.n), y = new Float64Array(this.n);
    const c = new Float64Array(this.n);
    const start = (this.head - this.n + this.cap) % this.cap;
    for (let i = 0; i < this.n; i++) {
      const k = (start + i) % this.cap;
      t[i] = this.t[k]; y[i] = this.y[k]; c[i] = this.c[k];
    }
    return { t, y, c, length: this.n };
  }
  clear() { this.n = 0; this.head = 0; }
}

export const PHASES = ['ACQUIRE', 'SOLVE', 'VERIFY', 'LOCK', 'APPROACH'];

/* SPIN_SPLIT mirrors `prec_f_max` in pipeline/period.py: above it, spin; below
   it, the Axis B envelope. The envelope routinely carries more power than the
   spin, so taking the global periodogram maximum returns the precession
   harmonic with total confidence. */
const SPIN_SPLIT = 0.5;

export class Engine {
  constructor(opts = {}) {
    this.cfg = {
      pSpin: TRUTH.pSpin,
      pPrec: TRUTH.pPrec,
      coneDeg: TRUTH.coneDeg,
      phaseDeg: TRUTH.phaseDeg,
      noise: 0.02,
      fps: 60,
      windowS: 60,
      speed: 1.0,
      dropout: 0,
      clip: false,
      ...opts,
    };

    this.facets = [];
    this.rng = new Rng(7);
    this.buf = new Ring(Math.ceil(this.cfg.windowS * this.cfg.fps * 1.3));
    this.solver = new GlsSolver({ fMin: 0.05, fMax: 2.6, oversample: 8, fPick: SPIN_SPLIT });

    this.gls = null;
    this.solve = null;
    this.walk = null;
    this.adopted = NaN;
    this.pPrec = NaN;
    this.precCycles = NaN;
    this.history = [];
    this.features = null;
    this.stage = 0;              // which pipeline stage last ran
    this.stageHits = new Array(9).fill(0);

    this.notifier = new NotificationEngine({ name: 'bench', threshold: 0.45, cooldownS: 12 });
    this.feed = [];
    this.log = [];

    this.simT = 0;
    this.sampleAcc = 0;
    this.lastSolveT = -99;
    this.fluxMax = 1e-9;
    this.running = true;
    this.phase = 0;
    this.phaseSince = 0;
    this.approach = 0;
    this.fault = null;

    this.onEvent = opts.onEvent || (() => {});
    this.onSolveDone = opts.onSolveDone || (() => {});
    this.onPhase = opts.onPhase || (() => {});
  }

  setFacets(facets) {
    this.facets = facets;
    this.reset();
    this.note(`facet set loaded — ${facets.length} facets`);
  }

  reset() {
    this.buf.clear();
    this.gls = null; this.solve = null; this.walk = null;
    this.adopted = NaN; this.pPrec = NaN; this.precCycles = NaN;
    this.history.length = 0;
    this.fluxMax = 1e-9;
    this.lastSolveT = this.simT - 99;
    this.setPhase(0);
  }

  resetAll() {
    this.reset();
    this.simT = 0;
    this.feed.length = 0;
    this.log.length = 0;
    this.notifier = new NotificationEngine({ name: 'bench', threshold: 0.45, cooldownS: 12 });
    this.clearFault();
    this.approach = 0;
    this.stageHits.fill(0);
  }

  /* ------------------------------------------------------------- faults */

  setFault(kind) {
    if (this.fault?.kind === kind) { this.clearFault(); return null; }
    this.clearFault();
    this.fault = { kind, start: this.simT, ramp: 14 };
    if (kind === 'saturate') this.cfg.clip = true;
    if (kind === 'dropout') this.cfg.dropout = 0.22;
    this.note(`fault injected — ${kind}`);
    return this.fault;
  }

  clearFault() {
    if (this.fault) this.note(`fault cleared — ${this.fault.kind}`);
    this.fault = null;
    this.cfg.clip = false;
    this.cfg.dropout = 0;
  }

  /** Commanded spin period now, including any injected fault. */
  currentSpin() {
    let p = this.cfg.pSpin;
    if (this.fault?.kind === 'spindown') {
      // Eddy-current damping against Earth's field is a ramp, not a step.
      const k = Math.min(1, Math.max(0, (this.simT - this.fault.start) / this.fault.ramp));
      p = this.cfg.pSpin * (1 + 0.21 * k);
    }
    return p;
  }

  params() {
    return {
      pSpin: this.currentSpin(),
      pPrec: this.cfg.pPrec,
      coneDeg: this.cfg.coneDeg,
      panelArea: 4000,
      spinPhase0: 0.37,
      precPhase0: 0.11,
      scale: 1, offset: 0, rhoD: 1,
    };
  }

  /** Instantaneous noise-free state, for the renderer. */
  stateAt(t) {
    if (!this.facets.length) return { total: 0, specular: 0, diffuse: 0, phi: 0, eps: 0 };
    return fluxAt(t, this.params(), this.facets, this.cfg.phaseDeg);
  }

  /* ---------------------------------------------------------------- tick */

  tick(dt) {
    if (!this.running) return;
    const sdt = dt * this.cfg.speed;
    this.simT += sdt;
    this.ingest(sdt);
    this.pump();
    this.advanceMission(sdt);
  }

  ingest(sdt) {
    if (!this.facets.length) return;
    const dtS = 1 / this.cfg.fps;
    this.sampleAcc += sdt;
    let guard = 0;
    while (this.sampleAcc >= dtS && guard++ < 240) {
      this.sampleAcc -= dtS;
      const tExact = this.simT - this.sampleAcc;

      // USB scheduling jitter: the sample times are data, not an index. This
      // is exactly why the solver is Lomb-Scargle and not an FFT.
      const t = tExact + this.rng.normal() * 0.0012;
      this.stageHits[1]++;                                   // S2 capture

      if (this.cfg.dropout > 0 && this.rng.next() < this.cfg.dropout) continue;

      const st = this.stateAt(tExact);
      this.stageHits[0]++;                                   // S1 forward model
      let f = st.total;
      if (this.fault?.kind === 'occult') f *= 0.04;
      if (f > this.fluxMax) this.fluxMax = f;

      const clean = f;
      const mean = Math.max(this.fluxMax * 0.35, 1e-9);
      f += this.rng.normal() * this.cfg.noise * mean;        // S3 photometry
      this.stageHits[2]++;
      if (this.cfg.clip) f = Math.min(f, this.fluxMax * 0.42);

      this.buf.push(t, f, clean);
    }
  }

  /** Run the solver on a duty cycle, sliced so a frame is never blocked. */
  pump() {
    if (!this.solver.done) {
      if (this.solver.step(3.5)) this.finishSolve();
      return;
    }
    if (this.simT - this.lastSolveT < 1.0) return;
    const snap = this.buf.snapshot();
    const span = snap.length ? snap.t[snap.length - 1] - snap.t[0] : 0;
    if (span < 6) return;
    this.lastSolveT = this.simT;
    this.snap = snap;
    this.stageHits[3]++;                                     // S4 detrend
    this.solver.begin(snap.t, snap.y);
    this.stage = 4;                                          // S5 running
  }

  finishSolve() {
    this.gls = { freq: this.solver.freq, power: this.solver.power };
    this.solve = this.solver.result;
    this.stageHits[4]++;
    if (!this.solve) return;

    // Sub-harmonic walk, each round on a curve flattened over its own fold
    // window — otherwise the precession envelope makes every cycle a different
    // height and the walk doubles the period forever.
    let P = this.solve.period;
    const trail = [];
    for (let i = 0; i < 3; i++) {
      const yf = flatten(this.snap.t, this.snap.y, 2.0 * P);
      const ft = foldTest(this.snap.t, yf, P);
      if (!ft) break;
      trail.push({ pHalf: P, pFull: 2 * P, chi2red: ft.chi2red, decision: ft.decision });
      if (ft.decision === 'fundamental') P = 2 * P; else break;
    }
    this.walk = trail;
    this.adopted = P;
    this.stageHits[5]++;

    // Axis B, from the fine-structure-averaged curve.
    const nS = this.snap.length;
    const dt = (this.snap.t[nS - 1] - this.snap.t[0]) / Math.max(1, nS - 1);
    const m = Math.max(1, Math.round(P / dt));
    const env = movingAverage(this.snap.y, m);
    const er = glsDirect(this.snap.t, env, 0.03, SPIN_SPLIT, 8);
    if (er) {
      const baseline = this.snap.t[nS - 1] - this.snap.t[0];
      let Pe = er.period;
      for (let i = 0; i < 2; i++) {
        // Never adopt an envelope period the baseline cannot hold four cycles of.
        if (2 * Pe > baseline / 4) break;
        const ft = foldTest(this.snap.t, env, Pe, 40, m);
        if (!ft || ft.decision !== 'fundamental') break;
        Pe *= 2;
      }
      this.pPrec = Pe;
      this.precCycles = baseline / Pe;
      this.env = env;
    }
    this.stageHits[6]++;

    this.history.push({ t: this.simT, period: P, significant: this.solve.significant });
    if (this.history.length > 260) this.history.shift();

    this.features = quickFeatures(this.snap.t, this.snap.y, this.solve);
    this.stageHits[7]++;
    this.detect();
    this.stageHits[8]++;
    this.onSolveDone(this);
  }

  /* ----------------------------------------------- anomalies and the gate */

  detect() {
    const f = this.features;
    if (!f) return;
    const events = [];
    const present = new Set();

    if (!this.solve.significant) {
      events.push({ kind: 'insignificant_period', severity: 'low', confidence: 0.7,
        detail: `FAP ${this.solve.fap.toExponential(1)} — not significant` });
    }
    if (Number.isFinite(f.modulationDepth) && f.modulationDepth < 0.05) {
      events.push({ kind: 'no_modulation', severity: 'critical', confidence: 0.95,
        detail: `modulation depth ${f.modulationDepth.toFixed(3)} — target effectively not seen` });
    }
    if (this.cfg.clip) {
      events.push({ kind: 'saturation', severity: 'critical', confidence: 0.99,
        detail: 'aperture clipping — every frame recorded now is being ruined' });
    }
    if (this.cfg.dropout > 0.05) {
      events.push({ kind: 'frame_dropouts', severity: 'medium', confidence: 0.9,
        detail: `${(this.cfg.dropout * 100).toFixed(0)} % of frames missing` });
    }
    if (Number.isFinite(f.snr) && f.snr < 6) {
      events.push({ kind: 'low_snr', severity: 'low', confidence: 0.6,
        detail: `SNR ${f.snr.toFixed(1)}` });
    }

    // Compare sustained medians, not single solves: the first solutions after
    // acquisition legitimately jump while the walk settles, and calling that a
    // change in the target would false-alarm on every healthy run.
    const h = this.history;
    if (h.length >= 12 && this.solve.significant) {
      const med = (a) => {
        const v = a.map((x) => x.period).sort((p, q) => p - q);
        return v[Math.floor(v.length / 2)];
      };
      const now = med(h.slice(-3));
      const ref = med(h.slice(-11, -5));
      const ratio = now / ref;
      const rel = Math.abs(ratio - 1);
      const harmonicFlip = Math.abs(ratio - 2) < 0.06 || Math.abs(ratio - 0.5) < 0.03;
      if (harmonicFlip) {
        events.push({ kind: 'morphology_change', severity: 'medium', confidence: 0.75,
          detail: `harmonic re-decided: P ${ref.toFixed(4)} s → ${now.toFixed(4)} s` });
      } else if (rel > 0.02) {
        events.push({ kind: 'period_change', severity: 'high',
          confidence: Math.min(0.99, 0.55 + rel * 8),
          detail: `P ${ref.toFixed(4)} s → ${now.toFixed(4)} s (${(rel * 100).toFixed(1)} %)` });
      }
    }

    for (const e of events) present.add(e.kind);
    this.notifier.observeAbsent(present);

    for (const e of events) {
      const d = this.notifier.submit(e, this.simT);
      this.feed.unshift(d);
      if (d.fired) {
        this.note(`${e.kind} — notified (I = ${d.importance.toFixed(3)})`, 'alert');
        if (e.kind === 'period_change') this.setPhase(1);
      }
      this.onEvent(d);
    }
    if (this.feed.length > 50) this.feed.length = 50;
  }

  /* ------------------------------------------------- mission sequencing */

  setPhase(p) {
    if (p === this.phase) return;
    this.phase = p;
    this.phaseSince = this.simT;
    if (p < 4) this.approach = 0;
    this.note(`phase → ${PHASES[p]}`);
    this.onPhase(p);
  }

  stability() {
    const h = this.history.slice(-5);
    if (h.length < 5) return Infinity;
    let lo = Infinity, hi = -Infinity;
    for (const x of h) { lo = Math.min(lo, x.period); hi = Math.max(hi, x.period); }
    return (hi - lo) / Math.max(lo, 1e-9);
  }

  advanceMission(sdt) {
    const sig = this.solve?.significant;
    const stab = this.stability();
    switch (this.phase) {
      case 0: if (sig && this.history.length >= 2) this.setPhase(1); break;
      case 1: if (sig && stab < 0.01) this.setPhase(2); break;
      case 2:
        if (!sig || stab > 0.02) this.setPhase(1);
        else if (this.simT - this.phaseSince > 6) this.setPhase(3);
        break;
      case 3:
        if (!sig || stab > 0.02) this.setPhase(1);
        else if (this.simT - this.phaseSince > 3) this.setPhase(4);
        break;
      case 4:
        if (!sig || stab > 0.03) { this.setPhase(1); break; }
        this.approach = Math.min(1, this.approach + sdt * 0.06);
        break;
    }
  }

  note(msg, kind = 'info') {
    this.log.unshift({ t: this.simT, msg, kind });
    if (this.log.length > 60) this.log.pop();
  }

  /** The harmonic the adopted period sits at, relative to the strongest peak. */
  harmonicOrder() {
    if (!this.solve || !Number.isFinite(this.adopted)) return NaN;
    return Math.round(this.adopted / this.solve.period);
  }
}
