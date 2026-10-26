/* ===========================================================================
   SPINDEX — the target catalogue
   ---------------------------------------------------------------------------
   Three real spacecraft, chosen because each one is a case the method has to
   handle and each one is a real object a servicer has had to think about.

   The geometry is NASA's. The models come from the NASA 3D Resources
   collection, which NASA publishes free and without copyright; they are used
   here unmodified apart from being centred, scaled and re-oriented so the body
   axes match the forward model's convention.

   `annotations` are positions in the normalised body frame (spin axis +z,
   wing span along x, panel normal along y), in units where the model's longest
   dimension is 1. They are clickable in the viewport.
   =========================================================================== */

export const TARGETS = {
  landsat7: {
    id: 'landsat7',
    file: 'assets/models/landsat7.glb',
    name: 'Landsat 7',
    role: 'Asymmetric — one array, offset instruments',
    status: 'Degraded since 2003, decommissioned 2024',
    mass: '≈ 1,970 kg',
    orbit: 'Sun-synchronous LEO, ≈ 705 km',
    why:
      'One large array on one side and an offset instrument stack on the other. '
      + 'The 180° symmetry is thoroughly broken, so consecutive half-cycles '
      + 'differ and the fold walk resolves the full period. Measured here: '
      + 'about 0.01 % against the commanded rate, at a modulation depth of 0.56.',
    harmonic: 'Resolved · depth 0.56',
    annotations: [
      { at: [-0.34, 0, 0.05], label: 'Solar array', part: 'array',
        note: 'The flat, strongly directional reflector. It makes the glints: face-on it returns a sharp specular spike, edge-on almost nothing.' },
      { at: [0.1, 0, 0.0], label: 'Spacecraft bus', part: 'bus',
        note: 'Multilayer insulation over the structure. Broad, multi-lobed scattering — this sets the baseline the glints rise above.' },
      { at: [0.34, 0.0, -0.1], label: 'Instrument / ETM+', part: 'instrument',
        note: 'The offset mass that breaks rotational symmetry. Without something like it the target would be degenerate at P/2.' },
      { at: [0.0, 0.0, 0.42], label: 'Spin axis +z', part: 'axis',
        note: 'Body +z. The cradle rock sweeps this axis through the observer\'s frame, which is what writes the precession envelope onto the curve.' },
    ],
  },

  ssl1300: {
    id: 'ssl1300',
    file: 'assets/models/ssl1300.glb',
    name: 'SSL-1300 bus',
    role: 'Symmetric box-wing — two opposed arrays',
    status: 'The class MEV-1 docked with in GEO (Intelsat 901, 2020)',
    mass: '≈ 2,200–6,700 kg depending on configuration',
    orbit: 'Geostationary',
    why:
      'Two opposed arrays over a box bus — the configuration that should be '
      + 'degenerate at P/2. It is not, and that is the point: the dishes, the '
      + 'offset antenna hardware and the asymmetric bus details break the '
      + '180° symmetry just enough for the fold walk to resolve the full '
      + 'period anyway. Real hardware is almost never exactly symmetric.',
    harmonic: 'Resolved · depth 1.01',
    annotations: [
      { at: [-0.4, 0, 0], label: 'Array, port', part: 'array',
        note: 'One of two opposed wings. Their symmetry is exactly what creates the harmonic ambiguity.' },
      { at: [0.4, 0, 0], label: 'Array, starboard', part: 'array2',
        note: 'Maps onto the port wing under a 180° rotation about the spin axis, so the two half-cycles are identical.' },
      { at: [0, 0, 0.0], label: 'Box bus', part: 'bus',
        note: 'The commercial GEO bus MEV-1 docked with. Its cross-section is not square, which keeps the ambiguity two-way rather than four-way.' },
      { at: [0.0, 0.18, -0.2], label: 'Communications dishes', part: 'dish',
        note: 'Narrow, strongly directional returns at particular aspects — short-lived spikes distinct from the array glints.' },
    ],
  },

  agena: {
    id: 'agena',
    file: 'assets/models/agena.glb',
    name: 'Agena upper stage',
    role: 'Spent stage — a cylinder with no arrays at all',
    status: 'The class of object ADRAS-J flew to in 2024',
    mass: '≈ 700 kg dry',
    orbit: 'LEO',
    why:
      'A spent stage has no solar array to glint, so the only structure in the '
      + 'curve comes from the engine bell, the fins and the aft equipment '
      + 'rack. The period still resolves, but the modulation depth is the '
      + 'lowest of the three at 0.40 — this is what a target with little to '
      + 'reflect off looks like, and where the false-alarm test starts earning '
      + 'its place.',
    harmonic: 'Resolved · depth 0.40',
    annotations: [
      { at: [0.32, 0, 0], label: 'Tank section', part: 'tank',
        note: 'A smooth cylinder returns almost the same flux at every rotation phase about its own axis. That is why the modulation depth falls.' },
      { at: [-0.3, 0, 0], label: 'Engine / aft end', part: 'engine',
        note: 'The asymmetric end. On a spent stage this is often the only feature writing structure into the curve.' },
      { at: [0, 0, 0.3], label: 'Spin axis +z', part: 'axis',
        note: 'Spent stages usually tumble about an axis set by residual propellant slosh and venting, not by design.' },
    ],
  },
  boxwingA: {
    id: 'boxwingA',
    procedural: 'A',
    file: null,
    name: 'Model A box-wing',
    role: 'The repository\'s idealised symmetric target',
    status: 'Printed on the bench; the validated Model A',
    mass: '≈ 0.4 kg printed',
    orbit: 'A bench, 400 mm from the lens',
    why:
      'Constructed to be exactly invariant under a 180° rotation about the '
      + 'spin axis: the +x wing maps onto the −x wing and the bus maps onto '
      + 'itself. The light curve is therefore exactly periodic at P/2 and no '
      + 'amount of photometry can tell P from P/2. The readout says P/2 and '
      + 'that is the correct answer — this is the case the three real '
      + 'spacecraft above are not, and the reason the fold walk exists.',
    harmonic: 'Genuinely degenerate at P/2',
    annotations: [
      { at: [-0.38, 0, 0], label: 'Panel, −x', part: 'array',
        note: 'Maps exactly onto the +x panel under a 180° rotation about the spin axis. That exactness is what makes the target degenerate.' },
      { at: [0.38, 0, 0], label: 'Panel, +x', part: 'array2',
        note: 'Acrylic-faced: flat and strongly directional, ρ_s = 2.6 with shininess 180. This is what makes the glints.' },
      { at: [0, 0, 0.1], label: 'Bus · 60 × 85 × 110 mm', part: 'bus',
        note: 'Creased aluminised mylar. The cross-section is deliberately not square — a square bus is 90°-symmetric and would turn a two-way ambiguity into a four-way one.' },
      { at: [0, 0, 0.45], label: 'Spin axis +z', part: 'axis',
        note: 'Body +z, driven by Axis A at 1,730 microsteps/s for P_A = 1.84971 s.' },
    ],
  },
};

export const TARGET_ORDER = ['landsat7', 'ssl1300', 'agena', 'boxwingA'];

/** The nine pipeline stages, as the strip under the viewport shows them. */
export const STAGES = [
  { id: 'S1', name: 'Forward model', note: 'facets × geometry → flux', module: 'forward_model.py' },
  { id: 'S2', name: 'Capture', note: '60 Hz, timestamped at grab', module: 'live.py' },
  { id: 'S3', name: 'Photometry', note: 'aperture sum, background annulus', module: 'photometry.py' },
  { id: 'S4', name: 'Detrend', note: 'linearise, unit mean, drift out', module: 'run_pipeline.py' },
  { id: 'S5', name: 'Lomb-Scargle', note: 'floating-mean periodogram', module: 'period.py' },
  { id: 'S6', name: 'Fold walk', note: 'P vs P/2, flattened each round', module: 'period.py' },
  { id: 'S7', name: 'Envelope', note: 'boxcar → Axis B period', module: 'period.py' },
  { id: 'S8', name: 'Features', note: '33 named features', module: 'features.py' },
  { id: 'S9', name: 'Gate', note: 'importance × 7 ordered gates', module: 'importance.py' },
];

/** Camera presets. `pos` and `look` are in scene units. */
export const VIEWS = [
  { id: 'wide', name: 'Whole scene', note: 'sun, target, observer', pos: [-3.1, 1.25, 4.5], look: [0, 0, 1.0] },
  { id: 'target', name: 'The derelict', note: 'close on the target', pos: [-1.9, 0.75, 2.1], look: [0, 0, 0] },
  { id: 'los', name: 'Down the line of sight', note: 'what the camera sees', pos: [0.04, 0.06, 4.1], look: [0, 0, 0] },
  { id: 'sun', name: 'From the sun', note: 'the lit hemisphere', pos: [2.6, 0.3, 4.5], look: [0, 0, 0] },
  { id: 'observer', name: 'Over the observer', note: 'behind the servicer', pos: [-1.5, 1.1, 7.4], look: [0, 0, 1.4] },
];
