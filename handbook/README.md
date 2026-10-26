# SPINDEX handbook

A deployable engineering handbook for SPINDEX, plus a dedicated **3D bench** page
where the measurement runs against real spacecraft geometry.

```
handbook/
  index.html              the handbook — twelve numbered sections
  bench.html              the 3D bench — its own page
  assets/css/             handbook.css (design system) + bench.css (bench layout)
  assets/js/
    sim.js                forward model, generalised Lomb-Scargle, fold walk,
                          features, importance gate — ports of the pipeline
    facets.js             reduces a real mesh to a facet set
    engine.js             the headless measurement engine (no DOM, no three.js)
    scene.js              the three.js scene: loading, alignment, picking, views
    targets.js            the target catalogue and the camera presets
    charts.js             light curve, periodogram, phase fold, period history
    bench.js              bench page controller
    app.js                handbook page behaviour
  assets/models/          NASA glTF spacecraft + the Draco decoder's input
  assets/draco/           Draco decoder, shipped locally
  assets/img/             figures and hardware photographs
  tools/make_figures.py   regenerates assets/img/hero_glint.png from the pipeline
  build_artifact.py       emits an Artifact-ready copy of index.html
```

## Run it locally

It must be a server, not `file://` — the pages are built from ES modules.

```bash
python -m http.server 8000 --directory handbook
# http://localhost:8000
```

For development, use the no-cache server so edits take effect:

```bash
python .claude/serve_handbook.py 8778
```

## Deploy it

No build step, no server-side component.

- **GitHub Pages** — Settings → Pages → deploy from branch, folder `/(root)`;
  served at `https://<user>.github.io/<repo>/handbook/`.
- **Netlify / Vercel / Cloudflare Pages** — publish directory `handbook`.
- **S3 / nginx** — upload `handbook/` as-is. Serve `.glb` as
  `model/gltf-binary` and `.wasm` as `application/wasm`.

## The 3D bench

`bench.html` is the part worth opening. Three real spacecraft from the NASA 3D
Resources collection tumble under the repository's own kinematics, and the
light curve is summed over facets extracted from the spacecraft's own
triangles — swap the target and the curve changes shape, because the
reflecting geometry did.

| Target | Role | What SPINDEX reports |
|---|---|---|
| **Landsat 7** | Asymmetric — one array, offset instruments | Period resolved, ≈ 0.01 % against commanded |
| **SSL-1300 bus** | Symmetric box-wing, two opposed arrays | `P/2` — genuinely degenerate |
| **Agena upper stage** | A near-cylinder with no array | Weak modulation; often not significant |

The whole inversion runs in the browser: the generalised Lomb-Scargle
periodogram, the sub-harmonic fold walk, the envelope search, the feature
extractor, the anomaly detectors and the seven-gate importance rule, once a
second on the last sixty seconds of samples.

### How a mesh becomes a facet set

The forward model sums over facets because the target is *unresolved*: only
normals, areas and reflectances matter. `facets.js` bins a model's triangles by
the direction they face (32 azimuth × 16 elevation) and by material class, adds
up the area in each bin and keeps the area-weighted mean normal. 40,439
Landsat triangles become 738 facets.

Material classes are assigned by a heuristic over each material's name and
colour. The kinematics, the periodogram and the fold walk are exact; the
absolute photometry of a specific real spacecraft is not claimed, and the
validated numbers in the handbook come from the box-wing targets.

## Agreement with the Python pipeline

`sim.js` is a direct port of `pipeline/forward_model.py` and the generalised
Lomb-Scargle in `pipeline/period.py`. On the box-wing targets it reproduces the
published result for both models, including the walk trail and Model A's
correct refusal to resolve `P` from `P/2`.

| | Python pipeline | This page |
|---|---|---|
| Model B, strongest spin-band peak | 2.1625 Hz | 2.1622 Hz |
| Model B, adopted spin period | 1.84970 s | 1.8499 s |
| Model B, walk decisions | fundamental → fundamental → half | identical |
| Model A, adopted spin period | 0.92485 s (`P/2`) | 0.92497 s (`P/2`) |

## Dependencies

three.js r169 from jsDelivr at a pinned version; fonts from Google Fonts. The
Draco decoder ships with the page rather than loading from a CDN, because the
pages must work from any static host and a sandboxed viewer will not fetch a
`.wasm` from a third-party origin. Everything else is in this directory.

Both pages degrade: if the browser has no WebGL context the bench says so and
points at the results, and the handbook itself is ordinary HTML.

## Notes for maintainers

- The solver splits the spectrum at **0.5 Hz**, mirroring `prec_f_max` in
  `pipeline/period.py`. The envelope routinely carries more power than the
  spin, so taking the global maximum returns the precession harmonic with
  total confidence.
- Each round of the fold walk flattens the curve over its own fold window.
  Without it the precession envelope makes every cycle a different height and
  the walk doubles the period forever.
- The periodogram is time-sliced across animation frames, so a solve never
  blocks the render loop.
- The renderer sets `preserveDrawingBuffer`, so the viewport can be
  screenshotted and `assets/img/bench_poster.jpg` can be taken from it.

## Credits

Spacecraft geometry from the **NASA 3D Resources** collection, which NASA
publishes free and without copyright. Landsat 7, the Space Systems Loral
SSL-1300 bus, the Agena target vehicle and the Shuttle Remote Manipulator
System are used unmodified apart from centring, scaling and re-orientation.
NASA does not endorse this project.
