/* ===========================================================================
   SPINDEX — orbital bench controller
   ---------------------------------------------------------------------------
   Drives three things that have to agree with each other: a clock, a set of
   real orbits propagated against it, and the photometric inversion running on
   whichever object the camera is pointed at.

   The link that matters is the phase angle. It is not a slider — it is the
   angle between the sun and the camera as measured at the target, recomputed
   every frame from the real solar vector and wherever you have flown to. Move
   the camera and the illumination geometry genuinely changes, so the light
   curve changes, so the inversion has different data to work with.
   =========================================================================== */

import { Engine, PHASES } from './engine.js';
import { OrbitScene } from './scene.js';
import { STAGES } from './targets.js';
import { drawLightCurve, drawPeriodogram, drawFold, drawHistory } from './charts.js';
import { pctErr } from './sim.js';
import {
  CATALOGUE, TLE_SNAPSHOT_ISO, attachSatelliteLib, initRecords,
  elementAgeDays, geodetic, speed, periodMinutes, inclinationDeg,
} from './orbit.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const RATES = [
  { label: 'real time', x: 1 },
  { label: '60 ×', x: 60 },
  { label: '600 ×', x: 600 },
];

class Bench {
  constructor() {
    this.simDate = new Date();
    this.rate = 1;

    this.engine = new Engine({
      onEvent: () => this.renderFeed(),
      onPhase: (p) => this.renderPhase(p),
      onSolveDone: () => this.renderSolve(),
    });

    this.catalogue = initRecords(CATALOGUE);

    this.scene = new OrbitScene($('#scene-canvas'), $('#scene-stage'), {
      catalogue: this.catalogue,
      onPick: (p) => this.renderSelection(p),
      onSelect: (id) => this.selectSatellite(id),
    });
    this.scene.buildCatalogue(this.simDate);

    this.buildSatelliteButtons();
    this.buildViewButtons();
    this.buildChain();
    this.buildReadout();
    this.bindControls();
    this.bindFaults();

    this.ro = new ResizeObserver(() => this.scene.resize());
    this.ro.observe($('#scene-stage'));

    this.host = $('#view-bench');
    this.lastFrame = performance.now();
    this.loop = this.loop.bind(this);

    $('#meta-geometry').textContent = 'NASA 3D Resources · CelesTrak elements';
    this.scene.showOrbitView(10);
    this.selectSatellite('landsat7').then(() => requestAnimationFrame(this.loop));
  }

  /* ------------------------------------------------------------- building */

  buildSatelliteButtons() {
    const host = $('#target-buttons');
    host.innerHTML = '';
    for (const s of this.catalogue) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'opt';
      b.dataset.target = s.id;
      b.innerHTML = `<b>${s.name}</b><span>${s.status}</span>`;
      b.addEventListener('click', () => this.selectSatellite(s.id));
      host.appendChild(b);
    }
  }

  buildViewButtons() {
    const host = $('#view-buttons');
    host.innerHTML = '';
    const add = (name, note, fn) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'opt';
      b.innerHTML = `<b>${name}</b><span>${note}</span>`;
      b.addEventListener('click', () => {
        $$('#view-buttons .opt').forEach((x) => x.classList.toggle('is-on', x === b));
        fn();
      });
      host.appendChild(b);
      return b;
    };
    this.btnOrbit = add('Orbit view', 'Earth and every track', () => this.scene.showOrbitView());
    this.btnInspect = add('Fly to the target', 'close on the selected object', () => {
      if (this.scene.targetEci) {
        this.scene.showInspectView(this.scene.targetEci,
          (this.scene.selected?.sizeM || 5) / 1000);
      }
    });
    this.btnInspect.classList.add('is-on');
  }

  buildChain() {
    const host = $('#chain');
    host.innerHTML = '';
    this.chainEls = STAGES.map((s) => {
      const el = document.createElement('div');
      el.className = 'chain-block';
      el.innerHTML = `<span class="n">${s.id}</span><b>${s.name}</b>`
        + `<span class="d">${s.note}</span><span class="m">${s.module}</span>`;
      host.appendChild(el);
      return el;
    });
  }

  buildReadout() {
    this.rows = [
      ['altitude', 'alt'], ['ground speed', 'spd'], ['sub-satellite point', 'geo'],
      ['orbital period', 'per'], ['inclination', 'inc'], ['illumination', 'ecl'],
      ['range from camera', 'rng'], ['phase angle', 'pha'], ['element age', 'age'],
      ['commanded P_A', 'cmdA'], ['recovered P_A', 'recA'], ['error on P_A', 'errA'],
      ['recovered P_B', 'recB'], ['LS peak', 'peak'], ['harmonic order', 'harm'],
      ['false-alarm probability', 'fap'], ['modulation depth', 'depth'], ['SNR', 'snr'],
    ];
    const host = $('#readout');
    host.innerHTML = '';
    this.ro_ = {};
    for (const [label, key] of this.rows) {
      const d = document.createElement('div');
      d.className = 'ro';
      d.innerHTML = `<span>${label}</span><b></b>`;
      host.appendChild(d);
      this.ro_[key] = d.querySelector('b');
    }
  }

  /* ------------------------------------------------------------ controls */

  bindControls() {
    const bind = (id, key, fmt, after) => {
      const el = $(id); if (!el) return;
      const out = $(`${id}-out`);
      const apply = () => {
        const v = parseFloat(el.value);
        if (key) this.engine.cfg[key] = v;
        if (out) out.textContent = fmt(v);
        if (after) after(v);
      };
      el.addEventListener('input', apply);
      apply();
    };
    bind('#c-pspin', 'pSpin', (v) => `${v.toFixed(4)} s`, () => this.engine.reset());
    bind('#c-pprec', 'pPrec', (v) => `${v.toFixed(2)} s`, () => this.engine.reset());
    bind('#c-cone', 'coneDeg', (v) => `${v.toFixed(0)}°`);
    bind('#c-noise', 'noise', (v) => `${(v * 100).toFixed(1)} %`);
    bind('#c-explode', null, (v) => `${Math.round(v * 100)} %`, (v) => this.scene.setExploded(v));

    const rateHost = $('#rate-buttons');
    if (rateHost) {
      rateHost.innerHTML = '';
      RATES.forEach((r, i) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = 'btn' + (i === 0 ? ' is-on' : '');
        b.textContent = r.label;
        b.addEventListener('click', () => {
          this.rate = r.x;
          $$('#rate-buttons .btn').forEach((x) => x.classList.toggle('is-on', x === b));
        });
        rateHost.appendChild(b);
      });
    }

    const play = $('#c-play');
    play?.addEventListener('click', () => {
      this.engine.running = !this.engine.running;
      play.textContent = this.engine.running ? 'Pause' : 'Resume';
      play.classList.toggle('is-on', !this.engine.running);
    });
    $('#c-reset')?.addEventListener('click', () => {
      this.engine.resetAll();
      $$('#fault-buttons .opt').forEach((x) => x.classList.remove('is-on'));
      this.renderLog();
    });

    const toggle = (id, fn, on0) => {
      const b = $(id); if (!b) return;
      let on = on0;
      b.addEventListener('click', () => { on = !on; b.classList.toggle('is-on', on); fn(on); });
    };
    toggle('#c-labels', (on) => this.scene.setLabelsVisible(on), true);
    toggle('#c-fan', (on) => this.scene.setTracksVisible(on), true);
    toggle('#c-orbit', (on) => this.scene.setAutoRotate(on), false);
  }

  bindFaults() {
    $$('[data-fault]').forEach((b) => b.addEventListener('click', () => {
      const f = this.engine.setFault(b.dataset.fault);
      $$('#fault-buttons .opt').forEach((x) => x.classList.remove('is-on'));
      if (f) b.classList.add('is-on');
      this.renderLog();
    }));
  }

  /* ----------------------------------------------------------- selection */

  async selectSatellite(id) {
    const sat = this.catalogue.find((s) => s.id === id);
    if (!sat) return;
    $$('#target-buttons .opt').forEach((x) => x.classList.toggle('is-on', x.dataset.target === id));
    $('#target-why').textContent = sat.note;
    $('#chip-target').textContent = sat.name.toUpperCase();
    $('#loading').style.display = '';
    this.engine.running = false;

    try {
      const { facets, stats } = await this.scene.loadModelFor(sat);
      this.scene.selected = sat;
      this.scene.buildAnnotations(sat);
      this.engine.setFacets(facets);
      $('#meta-facets').textContent = stats.exact
        ? `${stats.facets} exact facets`
        : `${stats.facets} from ${stats.triangles.toLocaleString()} triangles`;
      this.engine.note(`${sat.name} — NORAD ${sat.norad}, ${sat.designator}`);
      if (!sat.modelIsExact) {
        this.engine.note('mesh is a stand-in: no public-domain model of this object', 'alert');
      }
      const pv = this.scene.targetEci;
      this.scene.showInspectView(pv || { x: 0, y: 0, z: 0 }, (sat.sizeM || 5) / 1000);
      $$('#view-buttons .opt').forEach((x, i) => x.classList.toggle('is-on', i === 1));
    } catch (err) {
      console.error('[bench] could not load', err);
      this.engine.note(`could not load ${sat.name}: ${err.message}`, 'alert');
    }

    $('#loading').style.display = 'none';
    this.engine.running = true;
    $('#c-play').textContent = 'Pause';
    this.renderLog();
    this.renderSelection({ kind: 'satellite', sat });
  }

  /* ---------------------------------------------------------------- loop */

  loop(now) {
    const raw = (now - this.lastFrame) / 1000;
    this.lastFrame = now;
    const dt = Math.min(raw, 0.05);

    if (this.host && this.host.hidden) { requestAnimationFrame(this.loop); return; }

    // The clock is real unless the reader asks for it not to be.
    this.simDate = new Date(this.simDate.getTime() + dt * 1000 * this.rate);

    // Phase angle comes from the geometry, not from a control.
    const obs = this.scene.observation(this.simDate);
    if (obs) {
      this.engine.cfg.phaseDeg = obs.phaseDeg;
      this.engine.eclipsed = obs.eclipsed;
    }

    this.engine.tick(dt);

    const e = this.engine;
    const st = e.stateAt(e.simT);
    const dark = obs?.eclipsed || e.fault?.kind === 'occult';
    const shown = dark ? st.total * 0.02 : st.total;

    this.scene.update(this.simDate, {
      phi: st.phi, eps: st.eps,
      specFrac: dark ? 0 : st.specular / Math.max(st.total, 1e-9),
    }, dt);

    this.draw();
    this.renderLive(obs);
    requestAnimationFrame(this.loop);
  }

  draw() {
    const e = this.engine;
    const snap = e.buf.snapshot();
    drawLightCurve($('#plot-curve'), snap, { window: 30, period: e.adopted });
    drawPeriodogram($('#plot-periodogram'), e.gls, e.solve, e.currentSpin());
    drawFold($('#plot-fold'), snap, e.adopted);
    drawHistory($('#plot-history'), e.history, e.cfg.pSpin);
  }

  /* ------------------------------------------------------------ rendering */

  renderLive(obs) {
    const set = (k, v, cls) => {
      const el = this.ro_[k]; if (!el) return;
      el.textContent = v;
      if (cls !== undefined) el.className = cls;
    };
    const hud = (id, v) => { const el = $(id); if (el) el.textContent = v; };

    hud('#hud-clock', this.simDate.toISOString().replace('T', ' ').slice(0, 19) + ' UTC');

    if (!obs) return;
    const sat = this.scene.selected;
    const g = geodetic(obs.eci, this.simDate);
    set('alt', `${g.altKm.toFixed(1)} km`);
    set('spd', `${speed(obs.vel).toFixed(3)} km/s`);
    set('geo', `${g.latDeg.toFixed(2)}°, ${g.lonDeg.toFixed(2)}°`);
    set('per', `${periodMinutes(sat.satrec).toFixed(2)} min`);
    set('inc', `${inclinationDeg(sat.satrec).toFixed(3)}°`);
    set('ecl', obs.eclipsed ? 'in Earth’s shadow' : 'sunlit',
      obs.eclipsed ? 'is-warn' : 'is-good');
    set('rng', obs.rangeKm < 1 ? `${(obs.rangeKm * 1000).toFixed(0)} m`
      : `${obs.rangeKm.toFixed(1)} km`);
    set('pha', `${obs.phaseDeg.toFixed(1)}°`);
    const age = elementAgeDays(sat.satrec, this.simDate);
    set('age', `${age.toFixed(1)} d`, age > 30 ? 'is-warn' : '');

    hud('#hud-phase', `${obs.phaseDeg.toFixed(1)}°`);
    hud('#hud-ecl', obs.eclipsed ? 'ECLIPSE' : 'SUNLIT');
    $('#hud-readout')?.classList.toggle('is-dark', obs.eclipsed);
  }

  renderSolve() {
    const e = this.engine;
    const set = (k, v, cls) => {
      const el = this.ro_[k]; if (!el) return;
      el.textContent = v;
      if (cls !== undefined) el.className = cls;
    };
    const hud = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    const cmd = e.currentSpin();
    const f = e.features || {};

    hud('#hud-commanded', `${cmd.toFixed(5)} s`);
    hud('#hud-recovered', Number.isFinite(e.adopted) ? `${e.adopted.toFixed(5)} s` : '—');

    const ratio = cmd / e.adopted;
    let sub = 0;
    for (const n of [2, 4]) if (Math.abs(ratio - n) < 0.04 * n) sub = n;
    const err = pctErr(e.adopted, cmd);
    const errEl = $('#hud-error');
    if (errEl) {
      errEl.textContent = sub ? `P/${sub} · degenerate`
        : (Number.isFinite(err) ? `${err >= 0 ? '+' : ''}${err.toFixed(3)} %` : '—');
      errEl.className = 'hud-value ' + (sub ? 'is-warn'
        : (Math.abs(err) < 0.5 ? 'is-good' : 'is-warn'));
    }

    set('cmdA', `${cmd.toFixed(5)} s`);
    set('recA', Number.isFinite(e.adopted) ? `${e.adopted.toFixed(5)} s` : '—');
    set('errA', sub ? `P/${sub} degenerate`
      : (Number.isFinite(err) ? `${err >= 0 ? '+' : ''}${err.toFixed(3)} %` : '—'),
    sub ? 'is-warn' : (Math.abs(err) < 0.5 ? 'is-good' : 'is-warn'));
    set('recB', Number.isFinite(e.pPrec) ? `${e.pPrec.toFixed(3)} s` : '—');
    set('peak', e.solve ? `${(1 / e.solve.period).toFixed(5)} Hz` : '—');
    set('harm', Number.isFinite(e.harmonicOrder()) ? `× ${e.harmonicOrder()}` : '—');
    set('fap', e.solve ? e.solve.fap.toExponential(2) : '—',
      e.solve?.significant ? 'is-good' : 'is-warn');
    set('depth', Number.isFinite(f.modulationDepth) ? f.modulationDepth.toFixed(3) : '—');
    set('snr', Number.isFinite(f.snr) ? f.snr.toFixed(1) : '—');

    const fapEl = $('#hud-fap');
    if (fapEl && e.solve) {
      fapEl.textContent = e.solve.fap.toExponential(1);
      fapEl.className = 'hud-value ' + (e.solve.significant ? 'is-good' : 'is-muted');
    }
    $('#hud-readout')?.classList.toggle('is-insignificant', !e.solve?.significant);

    this.chainEls.forEach((el, i) => el.classList.toggle('is-live', e.stageHits[i] > 0));
    clearTimeout(this._t);
    this._t = setTimeout(() => this.chainEls.forEach((el) => el.classList.remove('is-live')), 700);
    e.stageHits.fill(0);

    this.renderWalk();
    this.renderLog();
    const s = e.notifier.stats;
    $('#gate-stats').textContent =
      `${s.submitted} submitted · ${s.fired} notified · ${s.suppressed} suppressed`;
  }

  renderWalk() {
    const host = $('#walk-trail');
    host.innerHTML = '';
    const walk = this.engine.walk || [];
    if (!walk.length) { host.innerHTML = '<li class="muted">waiting for the first solve</li>'; return; }
    for (const s of walk) {
      const li = document.createElement('li');
      li.className = s.decision === 'half' ? 'is-stop' : 'is-go';
      li.innerHTML = `<span class="mono">${s.pHalf.toFixed(4)} s → ${s.pFull.toFixed(4)} s</span>`
        + `<span class="mono">χ²<sub>red</sub> ${s.chi2red.toFixed(2)}</span>`
        + `<span class="tag">${s.decision === 'half' ? 'halves agree · stop' : 'halves differ · double'}</span>`;
      host.appendChild(li);
    }
  }

  renderFeed() {
    const host = $('#notify-feed');
    host.innerHTML = '';
    const feed = this.engine.feed;
    if (!feed.length) { host.innerHTML = '<li class="muted">No events.</li>'; return; }
    for (const d of feed.slice(0, 10)) {
      const li = document.createElement('li');
      li.className = d.fired ? 'is-fired' : 'is-suppressed';
      li.innerHTML = `<span class="nf-state">${d.fired ? 'NOTIFIED' : 'suppressed'}</span>`
        + `<span class="nf-kind">${d.event.kind}</span>`
        + `<span class="nf-detail">${d.event.detail}</span>`
        + `<span class="nf-score">I = ${d.importance.toFixed(3)} · ${d.urgency} · ${d.reason}</span>`;
      host.appendChild(li);
    }
  }

  renderLog() {
    const host = $('#event-log');
    host.innerHTML = '';
    for (const l of this.engine.log.slice(0, 14)) {
      const li = document.createElement('li');
      li.className = l.kind === 'alert' ? 'is-alert' : '';
      li.innerHTML = `<span class="mono">t+${l.t.toFixed(1)}s</span> ${l.msg}`;
      host.appendChild(li);
    }
  }

  renderPhase(p) {
    const el = $('#phase-chip');
    if (!el) return;
    el.textContent = PHASES[p];
    el.className = 'phase-chip ' + (p >= 3 ? 'is-lock' : p >= 1 ? 'is-solve' : '');
  }

  renderSelection(pick) {
    const host = $('#selected');
    if (!pick) return;

    if (pick.kind === 'satellite') {
      const s = pick.sat;
      host.innerHTML = `
        <h4>${s.name}</h4>
        <p>${s.note}</p>
        <dl class="kv">
          <div><dt>Catalogue</dt><dd>NORAD ${s.norad} · ${s.designator}</dd></div>
          <div><dt>Mass</dt><dd>${s.massKg.toLocaleString()} kg</dd></div>
          <div><dt>Size</dt><dd>${s.sizeM} m across</dd></div>
          <div><dt>Status</dt><dd>${s.status}</dd></div>
          <div><dt>Mesh</dt><dd>${s.modelIsExact ? 'the actual spacecraft' : 'stand-in — no public mesh exists'}</dd></div>
          <div><dt>Elements</dt><dd>CelesTrak, ${TLE_SNAPSHOT_ISO.slice(0, 10)}</dd></div>
        </dl>`;
      return;
    }
    if (pick.kind === 'annotation') {
      host.innerHTML = `<h4>${pick.data.label}</h4><p>${pick.data.note}</p>`;
      return;
    }
    host.innerHTML = `<h4>${pick.meshName}</h4>
      <dl class="kv">
        <div><dt>Material</dt><dd>${pick.materialName}</dd></div>
        <div><dt>Range</dt><dd>${pick.rangeM.toFixed(1)} m</dd></div>
      </dl>
      <p class="muted">The mesh the ray hit, and the material the facet extractor
      classified into one of four reflectance classes.</p>`;
  }
}

/* ---------------------------------------------------------------- startup */

let instance = null;

export function initBench() {
  if (instance) return instance;

  const probe = document.createElement('canvas');
  if (!(probe.getContext('webgl2') || probe.getContext('webgl'))) {
    $('#scene-stage').innerHTML =
      '<p class="fallback">This browser has no WebGL context, so the bench cannot run. '
      + 'The <a href="#results">results section</a> carries every number it would report.</p>';
    return null;
  }
  if (!window.satellite) {
    $('#scene-stage').innerHTML =
      '<p class="fallback">The SGP4 propagator did not load, so real orbits cannot be '
      + 'computed. Check the network, or read <a href="#results">section 07</a>.</p>';
    return null;
  }

  try {
    attachSatelliteLib(window.satellite);
    instance = new Bench();
    window.spindexBench = instance;
  } catch (err) {
    console.error('[bench] failed to start', err);
    $('#scene-stage').innerHTML =
      `<p class="fallback">The bench failed to start: ${err.message}</p>`;
  }
  return instance;
}
