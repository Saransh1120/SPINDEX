/* ===========================================================================
   SPINDEX — live diagnostic panels
   ---------------------------------------------------------------------------
   The three plots a servicer operator would actually watch: the scrolling
   light curve, the periodogram with its harmonic markers, and the phase fold
   that settles the P versus P/2 question. Plain 2D canvas, redrawn each frame.
   =========================================================================== */

const CSS = getComputedStyle(document.documentElement);
const C = (name, fallback) => (CSS.getPropertyValue(name) || fallback).trim();

const INK = () => C('--c-ink', '#e8eef7');
const MUTED = () => C('--c-muted', '#8795ab');
const ACCENT = () => C('--c-accent', '#4aa3ff');
const WARM = () => C('--c-warm', '#ff9f43');
const GOOD = () => C('--c-good', '#3ddc97');
const GRID = () => C('--c-grid', 'rgba(255,255,255,0.07)');

/** Size a canvas to its CSS box at device resolution and return a scaled ctx. */
export function fitCanvas(canvas) {
  const r = canvas.getBoundingClientRect();
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = Math.max(1, Math.round(r.width * dpr));
  const h = Math.max(1, Math.round(r.height * dpr));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w: r.width, h: r.height };
}

function axes(ctx, w, h, pad) {
  ctx.strokeStyle = GRID();
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (i * (h - pad.t - pad.b)) / 4;
    ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y);
  }
  ctx.stroke();
}

function text(ctx, s, x, y, { color = MUTED(), size = 10, weight = 500, align = 'left' } = {}) {
  ctx.fillStyle = color;
  ctx.font = `${weight} ${size}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = align;
  ctx.fillText(s, x, y);
}

/* ------------------------------------------------------ scrolling curve --- */

/**
 * The raw measurement. Everything downstream is derived from this one trace,
 * which is the point worth making visually: a single scalar against time.
 */
export function drawLightCurve(canvas, buf, opts = {}) {
  const { ctx, w, h } = fitCanvas(canvas);
  const pad = { l: 38, r: 10, t: 16, b: 20 };
  ctx.clearRect(0, 0, w, h);
  const n = buf.length;
  if (n < 2) return;

  const t = buf.t, y = buf.y;
  const tEnd = t[n - 1];
  const tStart = Math.max(t[0], tEnd - (opts.window ?? 30));
  let lo = Infinity, hi = -Infinity, i0 = 0;
  for (let i = 0; i < n; i++) {
    if (t[i] < tStart) { i0 = i; continue; }
    if (y[i] < lo) lo = y[i];
    if (y[i] > hi) hi = y[i];
  }
  if (!(hi > lo)) { hi = lo + 1; }
  const span = hi - lo;
  lo -= span * 0.12; hi += span * 0.18;

  const X = (tt) => pad.l + ((tt - tStart) / Math.max(1e-6, tEnd - tStart)) * (w - pad.l - pad.r);
  const Y = (vv) => h - pad.b - ((vv - lo) / (hi - lo)) * (h - pad.t - pad.b);

  axes(ctx, w, h, pad);

  // Fill under the trace, then the trace. The fill makes the glint spikes read
  // as events rather than as noise.
  ctx.beginPath();
  ctx.moveTo(X(t[i0]), h - pad.b);
  for (let i = i0; i < n; i++) ctx.lineTo(X(t[i]), Y(y[i]));
  ctx.lineTo(X(t[n - 1]), h - pad.b);
  ctx.closePath();
  const g = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
  g.addColorStop(0, 'rgba(74,163,255,0.28)');
  g.addColorStop(1, 'rgba(74,163,255,0.015)');
  ctx.fillStyle = g;
  ctx.fill();

  // The noise-free curve underneath, where the buffer carries it: the gap
  // between the two traces is the measurement error the inversion has to work
  // through, and showing it costs nothing because it was computed anyway.
  if (buf.c) {
    ctx.beginPath();
    for (let i = i0; i < n; i++) {
      const px = X(t[i]), py = Y(buf.c[i]);
      if (i === i0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.strokeStyle = MUTED();
    ctx.lineWidth = 1.1;
    ctx.globalAlpha = 0.75;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  ctx.beginPath();
  for (let i = i0; i < n; i++) {
    const px = X(t[i]), py = Y(y[i]);
    if (i === i0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.strokeStyle = ACCENT();
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Mark where the adopted period says the next glint should fall. When the
  // marks sit on the spikes, the solution is right.
  if (opts.period > 0.05) {
    ctx.strokeStyle = 'rgba(255,159,67,0.45)';
    ctx.setLineDash([3, 4]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let tt = tEnd; tt > tStart; tt -= opts.period) {
      ctx.moveTo(X(tt), pad.t); ctx.lineTo(X(tt), h - pad.b);
    }
    ctx.stroke();
    ctx.setLineDash([]);
  }

  text(ctx, 'flux', 6, pad.t + 4, { size: 9 });
  text(ctx, `${(tEnd - tStart).toFixed(0)} s window`, w - pad.r, h - 6,
    { size: 9, align: 'right' });
  text(ctx, `t = ${tEnd.toFixed(1)} s`, pad.l, h - 6, { size: 9 });
}

/* --------------------------------------------------------- periodogram --- */

/**
 * The generalised Lomb-Scargle power. The harmonic markers matter more than
 * the peak: a symmetric target puts its strongest power at 2/P or even 4/P,
 * and reporting that peak as the period is the classic way to be wrong by a
 * factor of two with high confidence.
 */
export function drawPeriodogram(canvas, gls, solve, truth) {
  const { ctx, w, h } = fitCanvas(canvas);
  const pad = { l: 38, r: 10, t: 16, b: 22 };
  ctx.clearRect(0, 0, w, h);
  if (!gls || !gls.freq) return;

  const f = gls.freq, p = gls.power;
  const fMin = f[0], fMax = f[f.length - 1];
  const X = (ff) => pad.l + ((ff - fMin) / (fMax - fMin)) * (w - pad.l - pad.r);
  const Y = (vv) => h - pad.b - vv * (h - pad.t - pad.b);

  axes(ctx, w, h, pad);

  ctx.beginPath();
  ctx.moveTo(X(fMin), h - pad.b);
  for (let i = 0; i < f.length; i++) ctx.lineTo(X(f[i]), Y(p[i]));
  ctx.lineTo(X(fMax), h - pad.b);
  ctx.closePath();
  const g = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
  g.addColorStop(0, 'rgba(61,220,151,0.30)');
  g.addColorStop(1, 'rgba(61,220,151,0.02)');
  ctx.fillStyle = g;
  ctx.fill();

  ctx.beginPath();
  for (let i = 0; i < f.length; i++) {
    const px = X(f[i]), py = Y(p[i]);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.strokeStyle = GOOD();
  ctx.lineWidth = 1.4;
  ctx.stroke();

  // Where the commanded rate and its harmonics actually are.
  if (truth > 0) {
    const marks = [[1 / truth, '1/P'], [2 / truth, '2/P'], [4 / truth, '4/P']];
    ctx.setLineDash([2, 3]);
    for (const [ff, lab] of marks) {
      if (ff < fMin || ff > fMax) continue;
      ctx.strokeStyle = 'rgba(255,159,67,0.5)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(X(ff), pad.t); ctx.lineTo(X(ff), h - pad.b);
      ctx.stroke();
      text(ctx, lab, X(ff) + 3, pad.t + 9, { size: 9, color: WARM() });
    }
    ctx.setLineDash([]);
  }

  if (solve) {
    const x = X(solve.freq);
    ctx.fillStyle = solve.significant ? ACCENT() : MUTED();
    ctx.beginPath();
    ctx.arc(x, Y(solve.power), 3.4, 0, Math.PI * 2);
    ctx.fill();
  }

  text(ctx, 'LS power', 6, pad.t + 4, { size: 9 });
  text(ctx, `${fMin.toFixed(2)} Hz`, pad.l, h - 7, { size: 9 });
  text(ctx, `${fMax.toFixed(2)} Hz`, w - pad.r, h - 7, { size: 9, align: 'right' });
}

/* ---------------------------------------------------------- phase fold --- */

/**
 * The curve folded at the adopted period, in two halves. If the halves lie on
 * top of each other, doubling the period buys nothing and the walk stops —
 * which is how a symmetric target correctly reports that it can only constrain
 * P/2.
 */
export function drawFold(canvas, buf, P, nbins = 48) {
  const { ctx, w, h } = fitCanvas(canvas);
  const pad = { l: 38, r: 10, t: 16, b: 20 };
  ctx.clearRect(0, 0, w, h);
  const n = buf.length;
  if (n < 64 || !(P > 0.05)) {
    text(ctx, 'waiting for a significant period', w / 2, h / 2, { align: 'center', size: 10 });
    return;
  }

  const t = buf.t, y = buf.y, t0 = t[0];
  const sum = new Float64Array(2 * nbins);
  const cnt = new Int32Array(2 * nbins);
  for (let i = 0; i < n; i++) {
    let ph = ((t[i] - t0) / (2 * P)) % 1.0;
    if (ph < 0) ph += 1;
    const b = Math.min(2 * nbins - 1, Math.floor(ph * 2 * nbins));
    sum[b] += y[i]; cnt[b]++;
  }
  let lo = Infinity, hi = -Infinity;
  const prof = new Float64Array(2 * nbins);
  for (let b = 0; b < 2 * nbins; b++) {
    prof[b] = cnt[b] ? sum[b] / cnt[b] : NaN;
    if (Number.isFinite(prof[b])) { lo = Math.min(lo, prof[b]); hi = Math.max(hi, prof[b]); }
  }
  if (!(hi > lo)) return;
  const sp = hi - lo; lo -= sp * 0.12; hi += sp * 0.12;

  const X = (u) => pad.l + u * (w - pad.l - pad.r);
  const Y = (v) => h - pad.b - ((v - lo) / (hi - lo)) * (h - pad.t - pad.b);

  axes(ctx, w, h, pad);

  for (const [half, color, width] of [[0, ACCENT(), 1.8], [1, WARM(), 1.4]]) {
    ctx.beginPath();
    let started = false;
    for (let b = 0; b < nbins; b++) {
      const v = prof[b + half * nbins];
      if (!Number.isFinite(v)) continue;
      const px = X((b + 0.5) / nbins), py = Y(v);
      if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.stroke();
  }

  text(ctx, 'folded at P', 6, pad.t + 4, { size: 9 });
  text(ctx, 'first half', pad.l + 2, h - 6, { size: 9, color: ACCENT() });
  text(ctx, 'second half', w - pad.r, h - 6, { size: 9, color: WARM(), align: 'right' });
}

/* ------------------------------------------------- period history strip --- */

/** Recovered period against time, with the commanded value as a reference. */
export function drawHistory(canvas, hist, truth) {
  const { ctx, w, h } = fitCanvas(canvas);
  const pad = { l: 38, r: 10, t: 12, b: 16 };
  ctx.clearRect(0, 0, w, h);
  if (!hist.length) return;

  let lo = truth, hi = truth;
  for (const p of hist) { if (p.period < lo) lo = p.period; if (p.period > hi) hi = p.period; }
  const sp = Math.max(hi - lo, truth * 0.02);
  lo -= sp * 0.4; hi += sp * 0.4;

  const t0 = hist[0].t, t1 = Math.max(hist[hist.length - 1].t, t0 + 1);
  const X = (tt) => pad.l + ((tt - t0) / (t1 - t0)) * (w - pad.l - pad.r);
  const Y = (v) => h - pad.b - ((v - lo) / (hi - lo)) * (h - pad.t - pad.b);

  ctx.strokeStyle = 'rgba(255,159,67,0.55)';
  ctx.setLineDash([4, 4]);
  ctx.beginPath(); ctx.moveTo(pad.l, Y(truth)); ctx.lineTo(w - pad.r, Y(truth)); ctx.stroke();
  ctx.setLineDash([]);

  ctx.beginPath();
  hist.forEach((p, i) => {
    const px = X(p.t), py = Y(p.period);
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  });
  ctx.strokeStyle = ACCENT();
  ctx.lineWidth = 1.6;
  ctx.stroke();

  for (const p of hist) {
    if (p.significant) continue;
    ctx.fillStyle = 'rgba(135,149,171,0.8)';
    ctx.beginPath(); ctx.arc(X(p.t), Y(p.period), 2, 0, Math.PI * 2); ctx.fill();
  }

  text(ctx, 'P (s)', 6, pad.t + 4, { size: 9 });
  text(ctx, 'commanded', w - pad.r, Y(truth) - 4, { size: 9, color: WARM(), align: 'right' });
}
