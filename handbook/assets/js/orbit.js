/* ===========================================================================
   SPINDEX — real orbits
   ---------------------------------------------------------------------------
   Every object in the viewport is a real catalogued satellite, propagated from
   its published two-line elements with SGP4 against the wall clock. Fly the
   camera anywhere and the geometry you are looking at — where the object is,
   where the sun is, whether it is in Earth's shadow, what phase angle you are
   observing it at — is computed, not staged.

   The elements below are a dated snapshot from CelesTrak, embedded rather than
   fetched because the page has to work from a static host and inside a
   sandboxed viewer that will not reach a third-party origin. SGP4 propagates a
   snapshot to the present perfectly well; what degrades with epoch age is
   along-track accuracy, which `elementAgeDays` reports so the page can say how
   stale it is rather than implying a precision it does not have.
   =========================================================================== */

/* Snapshot taken 2026-10-24 (epoch day 297.2 of 2026) from celestrak.org. */
export const TLE_SNAPSHOT_ISO = '2026-10-24T00:00:00Z';

export const CATALOGUE = [
  {
    id: 'landsat7',
    norad: 25682,
    name: 'Landsat 7',
    designator: '1999-020A',
    tle: [
      '1 25682U 99020A   26297.27650058  .00000328  00000+0  71004-4 0  9990',
      '2 25682  97.8651 295.0500 0001667  80.8573 289.7186 14.64007171464739',
    ],
    model: 'assets/models/landsat7.glb',
    modelIsExact: true,
    sizeM: 4.3,                       // bus long axis, for true-scale rendering
    massKg: 1973,
    status: 'Decommissioned 2024 · still in its operational orbit',
    note:
      'A real derelict in a crowded sun-synchronous shell, and the one object '
      + 'here whose published NASA mesh is the actual spacecraft. One large '
      + 'array and an offset instrument stack: the 180° symmetry is thoroughly '
      + 'broken, so the fold walk resolves the full period.',
  },
  {
    id: 'envisat',
    norad: 27386,
    name: 'Envisat',
    designator: '2002-009A',
    tle: [
      '1 27386U 02009A   26297.31460514  .00000049  00000+0  29716-4 0  9998',
      '2 27386  98.3995 245.8421 0001395  84.9129  75.8163 14.39089985292052',
    ],
    model: 'assets/models/landsat7.glb',
    modelIsExact: false,
    sizeM: 26,
    massKg: 8211,
    status: 'Failed April 2012 · 8.2 tonnes · the priority removal target',
    note:
      'The object every active-debris-removal study is written around: eight '
      + 'tonnes, uncontrolled since 2012, in one of the most congested orbits '
      + 'there is. Its tumble has been measured repeatedly from the ground by '
      + 'exactly the method this project implements — and it changed between '
      + 'campaigns, which is the argument for measuring it continuously.',
  },
  {
    id: 'intelsat901',
    norad: 26824,
    name: 'Intelsat 901',
    designator: '2001-024A',
    tle: [
      '1 26824U 01024A   26297.17795034  .00000054  00000+0  00000+0 0  9998',
      '2 26824   1.5895  82.7343 0001346  89.5341 149.9100  0.98818075 24199',
    ],
    model: 'assets/models/ssl1300.glb',
    modelIsExact: true,
    sizeM: 31,
    massKg: 4723,
    status: 'Geostationary · MEV-1 docked with it in February 2020',
    note:
      'An SSL-1300 bus, and the first satellite ever docked with by a servicer '
      + 'in orbit. The model here is that bus. Two opposed arrays should make '
      + 'it degenerate at P/2 — and it is not, because the dishes and offset '
      + 'hardware break the symmetry just enough.',
  },
  {
    id: 'sl16',
    norad: 23088,
    name: 'SL-16 R/B',
    designator: '1994-023B',
    tle: [
      '1 23088U 94023B   26297.33032128 -.00000224  00000+0 -90875-4 0  9999',
      '2 23088  70.9995 310.7149 0005579 293.5779  66.4757 14.15036331678435',
    ],
    model: 'assets/models/agena.glb',
    modelIsExact: false,
    sizeM: 11,
    massKg: 8900,
    status: 'Spent Zenit-2 second stage · abandoned 1994',
    note:
      'A nine-tonne rocket body, the class of object that dominates the mass '
      + 'of removable debris. No solar array to glint off, so the modulation '
      + 'depth is the lowest here and the false-alarm test starts earning its '
      + 'place. Rendered with an Agena stage as a stand-in.',
  },
  {
    id: 'hst',
    norad: 20580,
    name: 'Hubble',
    designator: '1990-037B',
    tle: [
      '1 20580U 90037B   26297.30580476  .00006132  00000+0  18497-3 0  9996',
      '2 20580  28.4737 277.2190 0002082 145.9091 214.1639 15.32067126808383',
    ],
    model: 'assets/models/ssl1300.glb',
    modelIsExact: false,
    sizeM: 13.2,
    massKg: 11110,
    status: 'Operational · serviced five times, reboost studied since',
    note:
      'Here because it is the counter-example: a cooperative target, attitude '
      + 'known to arcseconds, serviced five times by crews who knew exactly '
      + 'what it was doing. Everything else on this list is the opposite, and '
      + 'that difference is the whole problem.',
  },
];

/* ------------------------------------------------------------- constants */

export const EARTH_R_KM = 6371.0;
export const AU_KM = 149597870.7;
const DEG = Math.PI / 180;

/* ------------------------------------------------------------ SGP4 wrap -- */

let satlib = null;

/** satellite.js ships as a UMD bundle and defines a global. */
export function attachSatelliteLib(lib) { satlib = lib; }

export function initRecords(catalogue = CATALOGUE) {
  if (!satlib) throw new Error('satellite.js has not been attached');
  for (const s of catalogue) {
    s.satrec = satlib.twoline2satrec(s.tle[0], s.tle[1]);
  }
  return catalogue;
}

/** Days between a record's element epoch and `date`. */
export function elementAgeDays(satrec, date = new Date()) {
  // satrec.jdsatepoch is the Julian date of the elements.
  const jdNow = date.getTime() / 86400000 + 2440587.5;
  return jdNow - satrec.jdsatepoch;
}

/**
 * Position and velocity in the Earth-centred inertial frame, in km and km/s.
 * Returns null when SGP4 reports a decayed or diverged element set.
 */
export function propagate(satrec, date) {
  const pv = satlib.propagate(satrec, date);
  if (!pv || !pv.position || Number.isNaN(pv.position.x)) return null;
  return { r: pv.position, v: pv.velocity };
}

/** Sub-satellite latitude, longitude and altitude, from the ECI position. */
export function geodetic(rEci, date) {
  const gmst = satlib.gstime(date);
  const g = satlib.eciToGeodetic(rEci, gmst);
  return {
    latDeg: satlib.degreesLat(g.latitude),
    lonDeg: satlib.degreesLong(g.longitude),
    altKm: g.height,
    gmst,
  };
}

/** Greenwich mean sidereal time, which is how the Earth mesh gets oriented. */
export function gmstAt(date) { return satlib.gstime(date); }

/* ---------------------------------------------------------- solar vector */

/**
 * Unit vector from Earth's centre to the sun, in ECI, to about 0.01°.
 *
 * Low-precision almanac series — more than enough to decide which hemisphere
 * is lit, where the terminator falls, and what phase angle an observer sees.
 */
export function sunVectorEci(date) {
  const jd = date.getTime() / 86400000 + 2440587.5;
  const n = jd - 2451545.0;                       // days from J2000.0
  const L = (280.460 + 0.9856474 * n) * DEG;      // mean longitude
  const g = (357.528 + 0.9856003 * n) * DEG;      // mean anomaly
  const lambda = L + (1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * DEG;
  const eps = (23.439 - 0.0000004 * n) * DEG;     // obliquity
  return {
    x: Math.cos(lambda),
    y: Math.cos(eps) * Math.sin(lambda),
    z: Math.sin(eps) * Math.sin(lambda),
  };
}

/**
 * Is the satellite in Earth's shadow?
 *
 * Cylindrical umbra: exact enough to get the terminator crossings right, and
 * it is what matters here — an eclipsed target reflects nothing, so the light
 * curve stops and the solver has to say so.
 */
export function inEclipse(rEci, sunUnit) {
  const dot = rEci.x * sunUnit.x + rEci.y * sunUnit.y + rEci.z * sunUnit.z;
  if (dot > 0) return false;                      // sunward of Earth's centre
  const r2 = rEci.x * rEci.x + rEci.y * rEci.y + rEci.z * rEci.z;
  const perp2 = r2 - dot * dot;                   // distance from the shadow axis
  return perp2 < EARTH_R_KM * EARTH_R_KM;
}

/* ------------------------------------------------------------- geometry */

/** Phase angle at the target between the sun and an observer, in degrees. */
export function phaseAngleDeg(targetEci, observerEci, sunUnit) {
  const ox = observerEci.x - targetEci.x;
  const oy = observerEci.y - targetEci.y;
  const oz = observerEci.z - targetEci.z;
  const on = Math.hypot(ox, oy, oz) || 1;
  const dot = (ox / on) * sunUnit.x + (oy / on) * sunUnit.y + (oz / on) * sunUnit.z;
  return Math.acos(Math.min(1, Math.max(-1, dot))) / DEG;
}

/** Orbital period in minutes, straight from the mean motion SGP4 holds. */
export function periodMinutes(satrec) {
  return (2 * Math.PI) / satrec.no;               // no is rad/min
}

export function inclinationDeg(satrec) { return satrec.inclo / DEG; }
export function eccentricity(satrec) { return satrec.ecco; }

/** One full revolution of ECI positions, for drawing the ground-independent track. */
export function orbitTrack(satrec, date, samples = 360) {
  const periodMin = periodMinutes(satrec);
  const pts = [];
  for (let i = 0; i <= samples; i++) {
    const t = new Date(date.getTime() + (i / samples) * periodMin * 60000);
    const pv = propagate(satrec, t);
    if (pv) pts.push(pv.r);
  }
  return pts;
}

/** Speed in km/s from an SGP4 velocity vector. */
export function speed(v) { return Math.hypot(v.x, v.y, v.z); }
