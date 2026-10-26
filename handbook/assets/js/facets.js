/* ===========================================================================
   SPINDEX — facet extraction from a real spacecraft mesh
   ---------------------------------------------------------------------------
   The repository's forward model sums a Lambertian term and a Blinn-Phong lobe
   over a handful of facets, because the target is unresolved: at operational
   range only facet normals, areas and reflectances matter.

   A NASA model of a real satellite is the same thing at higher resolution —
   tens of thousands of triangles, each with a normal, an area and a material.
   Summing over all of them every frame would be wasteful and pointless, since
   thousands of triangles share a normal to within a degree.

   So the mesh is reduced the way the physics already implies: bin triangles by
   the direction they face and by what they are made of, add up the area in
   each bin, and keep the area-weighted mean normal. A 40,000-triangle Landsat
   collapses to a few hundred facets that reproduce its directional scattering,
   and the light curve this page draws is then computed from the real
   spacecraft's own geometry rather than from a stand-in.
   =========================================================================== */

import * as THREE from 'three';

/* Reflectance classes. The numbers are the repository's own, from
   pipeline/forward_model.py — creased aluminised mylar for the bus, an acrylic
   facing for the panels, bare printed PLA for the panel backs. */
export const CLASSES = {
  panel: { rhoD: 0.06, rhoS: 2.6, shine: 180.0, isPanel: true, label: 'solar array' },
  foil: { rhoD: 0.35, rhoS: 0.25, shine: 8.0, isPanel: false, label: 'MLI / foil' },
  metal: { rhoD: 0.22, rhoS: 0.9, shine: 40.0, isPanel: false, label: 'bare metal' },
  body: { rhoD: 0.30, rhoS: 0.20, shine: 12.0, isPanel: false, label: 'structure' },
};

/**
 * Guess what a material is from its name and its PBR parameters.
 *
 * The models carry no semantic tags, so this reads what the author left: the
 * material name where there is one, otherwise the colour and metalness. It is
 * a heuristic, and the page says so.
 */
export function classify(mat) {
  const name = (mat?.name || '').toLowerCase();
  if (/solar|panel|cell|array|photovolt|blue/.test(name)) return 'panel';
  if (/foil|mylar|mli|kapton|gold|silver|krinkle|crinkle|blanket/.test(name)) return 'foil';
  if (/alum|metal|steel|chrome|shiny|polish/.test(name)) return 'metal';

  const c = mat?.color;
  if (c) {
    const { r, g, b } = c;
    const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    // Solar cells read as dark and distinctly blue.
    if (b > r * 1.25 && b > g * 1.1 && lum < 0.34) return 'panel';
    // Gold MLI: warm, bright, saturated.
    if (r > 0.45 && r > b * 1.5 && lum > 0.3) return 'foil';
  }
  if ((mat?.metalness ?? 0) > 0.6) return 'metal';
  return 'body';
}

/* --------------------------------------------------------------- binning --- */

/* Azimuth x elevation bins. 32 x 16 puts a bin about 11 degrees across, which
   is finer than the Blinn-Phong lobe of the bus (shininess 8) and comparable
   to the panel lobe (shininess 180) at the resolution that matters here. */
const N_AZ = 32;
const N_EL = 16;

function binIndex(x, y, z) {
  const az = Math.atan2(y, x);                       // -pi .. pi
  const el = Math.acos(THREE.MathUtils.clamp(z, -1, 1)); // 0 .. pi
  const ia = Math.min(N_AZ - 1, Math.floor(((az + Math.PI) / (2 * Math.PI)) * N_AZ));
  const ie = Math.min(N_EL - 1, Math.floor((el / Math.PI) * N_EL));
  return ie * N_AZ + ia;
}

/**
 * Reduce an Object3D to a facet set in the frame of `root`.
 *
 * @param {THREE.Object3D} root  the aligned model; facet normals come out in
 *                               this object's local frame, which is the body
 *                               frame the forward model expects (spin axis +z)
 * @returns {{facets: Array, stats: object}}
 */
export function extractFacets(root) {
  root.updateWorldMatrix(true, true);
  const inv = new THREE.Matrix4().copy(root.matrixWorld).invert();

  // bins[class][binIndex] = {ax, ay, az (area-weighted normal sum), area}
  const bins = new Map();
  const classArea = { panel: 0, foil: 0, metal: 0, body: 0 };

  const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
  const ab = new THREE.Vector3(), ac = new THREE.Vector3(), n = new THREE.Vector3();
  const toBody = new THREE.Matrix4();

  let triangles = 0, meshes = 0;

  root.traverse((o) => {
    if (!o.isMesh || !o.geometry) return;
    const geo = o.geometry;
    const pos = geo.attributes.position;
    if (!pos) return;

    meshes++;
    toBody.multiplyMatrices(inv, o.matrixWorld);

    const mats = Array.isArray(o.material) ? o.material : [o.material];
    const groups = geo.groups && geo.groups.length
      ? geo.groups
      : [{ start: 0, count: (geo.index ? geo.index.count : pos.count), materialIndex: 0 }];
    const index = geo.index;

    for (const grp of groups) {
      const cls = classify(mats[grp.materialIndex ?? 0] || mats[0]);
      let bucket = bins.get(cls);
      if (!bucket) { bucket = new Map(); bins.set(cls, bucket); }

      const end = grp.start + grp.count;
      for (let i = grp.start; i + 2 < end; i += 3) {
        const i0 = index ? index.getX(i) : i;
        const i1 = index ? index.getX(i + 1) : i + 1;
        const i2 = index ? index.getX(i + 2) : i + 2;

        a.fromBufferAttribute(pos, i0).applyMatrix4(toBody);
        b.fromBufferAttribute(pos, i1).applyMatrix4(toBody);
        c.fromBufferAttribute(pos, i2).applyMatrix4(toBody);

        ab.subVectors(b, a);
        ac.subVectors(c, a);
        n.crossVectors(ab, ac);
        const twiceArea = n.length();
        if (twiceArea < 1e-12) continue;              // degenerate triangle
        const area = 0.5 * twiceArea;
        n.divideScalar(twiceArea);                    // unit normal

        triangles++;
        classArea[cls] += area;

        const k = binIndex(n.x, n.y, n.z);
        let e = bucket.get(k);
        if (!e) { e = { x: 0, y: 0, z: 0, area: 0 }; bucket.set(k, e); }
        e.x += n.x * area; e.y += n.y * area; e.z += n.z * area;
        e.area += area;
      }
    }
  });

  // Flatten to the facet shape pipeline/forward_model.py works in.
  const facets = [];
  let total = 0;
  for (const [cls, bucket] of bins) {
    const props = CLASSES[cls];
    for (const e of bucket.values()) {
      const len = Math.hypot(e.x, e.y, e.z);
      if (len < 1e-12) continue;
      facets.push({
        normal: [e.x / len, e.y / len, e.z / len],
        area: e.area,
        rhoD: props.rhoD, rhoS: props.rhoS, shine: props.shine,
        isPanel: props.isPanel,
        label: cls,
      });
      total += e.area;
    }
  }

  // Normalise total area so one model is not a hundred times brighter than
  // another purely because its author worked in different units.
  const scale = total > 0 ? 24000 / total : 1;
  for (const f of facets) f.area *= scale;

  // Drop slivers: facets under a thousandth of a percent of the area cannot
  // move the curve and only cost time in the inner loop.
  const floor = 24000 * 1e-5;
  const kept = facets.filter((f) => f.area > floor);

  return {
    facets: kept,
    stats: {
      meshes,
      triangles,
      facets: kept.length,
      binned: facets.length,
      // `total` is the pre-scaling area sum, which is what classArea is in too.
      classFractions: Object.fromEntries(
        Object.entries(classArea).map(([k, v]) => [k, total > 0 ? v / total : 0])),
    },
  };
}
