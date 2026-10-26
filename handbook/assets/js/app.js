/* ===========================================================================
   SPINDEX handbook — page behaviour
   ---------------------------------------------------------------------------
   Navigation, theme, copy buttons and reveal. The live model lives on its own
   page now (bench.html), driven by engine.js and scene.js.
   =========================================================================== */

/* ======================================================== page behaviour === */

function setupNav() {
  const links = Array.from(document.querySelectorAll('.toc a'));
  const map = new Map(links.map((a) => [a.getAttribute('href').slice(1), a]));
  const obs = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      links.forEach((a) => a.classList.remove('is-current'));
      map.get(e.target.id)?.classList.add('is-current');
    }
  }, { rootMargin: '-45% 0px -50% 0px', threshold: 0 });
  document.querySelectorAll('section[id]').forEach((s) => obs.observe(s));

  const toggle = document.querySelector('#nav-toggle');
  const side = document.querySelector('.sidebar');
  toggle?.addEventListener('click', () => side.classList.toggle('is-open'));
  side?.addEventListener('click', (e) => {
    if (e.target.tagName === 'A') side.classList.remove('is-open');
  });
}

function setupTheme() {
  const btn = document.querySelector('#theme-toggle');
  const root = document.documentElement;
  btn?.addEventListener('click', () => {
    const next = root.dataset.theme === 'light' ? 'dark' : 'light';
    root.dataset.theme = next;
    try { localStorage.setItem('spindex-theme', next); } catch { /* private mode */ }
    btn.setAttribute('aria-label', `Switch to ${next === 'light' ? 'dark' : 'light'} theme`);
  });
}

function setupCopyButtons() {
  document.querySelectorAll('pre > code').forEach((code) => {
    const pre = code.parentElement;
    const b = document.createElement('button');
    b.className = 'copy';
    b.type = 'button';
    b.textContent = 'Copy';
    b.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(code.textContent.trim());
        b.textContent = 'Copied';
      } catch { b.textContent = 'Select manually'; }
      setTimeout(() => { b.textContent = 'Copy'; }, 1600);
    });
    pre.appendChild(b);
  });
}

/** The nine-stage chain, with a token walking it so the order is unmissable. */
function setupPipeline() {
  const stages = Array.from(document.querySelectorAll('#pipeline .stage'));
  if (!stages.length) return;
  let i = 0;
  const tick = () => {
    stages.forEach((s, k) => s.classList.toggle('is-live', k === i));
    i = (i + 1) % stages.length;
  };
  tick();
  let timer = setInterval(tick, 900);
  const pipe = document.querySelector('#pipeline');
  pipe.addEventListener('mouseenter', () => clearInterval(timer));
  pipe.addEventListener('mouseleave', () => { timer = setInterval(tick, 900); });
}

/** Reveal-on-scroll, skipped entirely when the viewer asked for less motion. */
function setupReveal() {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.querySelectorAll('[data-reveal]').forEach((el) => el.classList.add('is-in'));
    return;
  }
  // Threshold 0 with a bottom margin, not a ratio: a section taller than the
  // viewport can never reach a ratio threshold, and the content would never
  // appear. A reveal effect must not be able to hide the document.
  const obs = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) { e.target.classList.add('is-in'); obs.unobserve(e.target); }
    }
  }, { threshold: 0, rootMargin: '0px 0px -8% 0px' });
  document.querySelectorAll('[data-reveal]').forEach((el) => obs.observe(el));
}


/* ----------------------------------------------------------------- router */

/**
 * Two views in one document: the handbook and the 3D bench.
 *
 * They are not two pages because a sandboxed viewer intercepts link clicks and
 * will not navigate to a second HTML file — only a bare `#token` reaches the
 * page. So the bench is a view, addressable as `#bench`, and its heavy modules
 * load the first time it is opened.
 */
function setupRouter() {
  const handbook = document.querySelector('#view-handbook');
  const bench = document.querySelector('#view-bench');
  if (!handbook || !bench) return;

  let benchStarted = false;

  const show = (wantBench, scrollTo) => {
    handbook.hidden = wantBench;
    bench.hidden = !wantBench;
    document.body.classList.toggle('is-bench', wantBench);

    if (wantBench && !benchStarted) {
      benchStarted = true;
      import('./bench.js')
        .then((m) => m.initBench())
        .catch((err) => {
          console.error('[spindex] bench failed to load', err);
          const stage = document.querySelector('#scene-stage');
          if (stage) {
            stage.innerHTML =
              `<p class="fallback">The bench could not load: ${err.message}</p>`;
          }
        });
    }

    if (wantBench) {
      window.scrollTo(0, 0);
    } else if (scrollTo) {
      // The handbook was `hidden` a moment ago. Force layout by reading a
      // geometry property, then scroll in the same tick — waiting for an
      // animation frame is unreliable in a backgrounded or throttled tab.
      void handbook.offsetHeight;
      const el = document.querySelector(`#${CSS.escape(scrollTo)}`);
      if (el) {
        const top = el.getBoundingClientRect().top + window.scrollY
          - (document.querySelector('.topnav')?.offsetHeight ?? 0) - 8;
        window.scrollTo(0, Math.max(0, top));
      }
    }
  };

  const route = () => {
    const token = location.hash.replace(/^#/, '');
    show(token === 'bench', token && token !== 'top' ? token : null);
  };

  window.addEventListener('hashchange', route);
  route();
}

/* ---------------------------------------------------------------- startup */

function boot() {
  setupRouter();
  setupNav();
  setupTheme();
  setupCopyButtons();
  setupPipeline();
  setupReveal();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
