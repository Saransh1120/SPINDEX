/* ===========================================================================
   SPINDEX — orbital scene
   ---------------------------------------------------------------------------
   Real objects, real positions, real light.

   The scene works in kilometres in the Earth-centred inertial frame. Every
   satellite is propagated from its published elements with SGP4 against the
   wall clock, the Earth is oriented by Greenwich sidereal time so the
   terminator falls where it actually falls, and the sun is placed on its real
   vector at one astronomical unit. Nothing is staged: fly to an object and the
   phase angle you are observing it at is computed from where you put the
   camera.

   Two things make the precision work at this scale. The scene origin is moved
   to whatever you are looking at, so a four-metre spacecraft seven thousand
   kilometres from Earth's centre is still rendered near zero where float
   precision is good. And the depth buffer is logarithmic, so a near plane of
   half a metre and a sun at 150 million kilometres can coexist.
   =========================================================================== */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { OutlinePass } from 'three/addons/postprocessing/OutlinePass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { Lensflare, LensflareElement } from 'three/addons/objects/Lensflare.js';

import { MODELS } from './sim.js';
import { extractFacets } from './facets.js';
import {
  EARTH_R_KM, AU_KM, propagate, sunVectorEci, inEclipse, gmstAt,
  orbitTrack, phaseAngleDeg,
} from './orbit.js';

const TEX = 'assets/textures/';
const V = (o) => new THREE.Vector3(o.x, o.y, o.z);

/* ------------------------------------------------------------- star sky -- */

function makeStarSky() {
  const w = 2048, h = 1024;
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d');
  g.fillStyle = '#000103';
  g.fillRect(0, 0, w, h);

  const tilt = 0.42, phase = 1.1;
  for (let pass = 0; pass < 3; pass++) {
    const width = [120, 54, 22][pass];
    const alpha = [0.035, 0.04, 0.05][pass];
    for (let x = 0; x < w; x += 2) {
      const lon = (x / w) * Math.PI * 2;
      const y = h / 2 + Math.sin(lon + phase) * h * tilt * 0.5;
      const grd = g.createLinearGradient(0, y - width, 0, y + width);
      grd.addColorStop(0, 'rgba(120,140,190,0)');
      grd.addColorStop(0.5, `rgba(182,196,224,${alpha})`);
      grd.addColorStop(1, 'rgba(120,140,190,0)');
      g.fillStyle = grd;
      g.fillRect(x, y - width, 3, width * 2);
    }
  }
  for (let i = 0; i < 240; i++) {
    const x = Math.random() * w;
    const lon = (x / w) * Math.PI * 2;
    const y = h / 2 + Math.sin(lon + phase) * h * tilt * 0.5 + (Math.random() - 0.5) * 90;
    g.fillStyle = `rgba(0,1,3,${0.12 + Math.random() * 0.24})`;
    g.beginPath();
    g.ellipse(x, y, 14 + Math.random() * 70, 3 + Math.random() * 9,
      Math.random() * 0.6 - 0.3, 0, Math.PI * 2);
    g.fill();
  }

  const COLOURS = ['170,198,255', '202,218,255', '236,241,255',
    '255,250,236', '255,230,190', '255,200,156'];
  const star = (x, y, r, col, a) => {
    const grd = g.createRadialGradient(x, y, 0, x, y, r);
    grd.addColorStop(0, `rgba(${col},${a})`);
    grd.addColorStop(0.35, `rgba(${col},${a * 0.45})`);
    grd.addColorStop(1, `rgba(${col},0)`);
    g.fillStyle = grd;
    g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill();
  };
  for (let i = 0; i < 9000; i++) {
    const x = Math.random() * w;
    const lon = (x / w) * Math.PI * 2;
    const band = h / 2 + Math.sin(lon + phase) * h * tilt * 0.5;
    const y = Math.random() < 0.45
      ? THREE.MathUtils.clamp(band + (Math.random() - 0.5) * 190, 0, h)
      : Math.random() * h;
    const mag = Math.pow(Math.random(), 7);
    star(x, y, 0.4 + mag * 2.1, COLOURS[(Math.random() * COLOURS.length) | 0],
      0.15 + mag * 0.7);
  }

  const tex = new THREE.CanvasTexture(c);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = false;
  return tex;
}

function radialTexture(stops) {
  const n = 256;
  const c = document.createElement('canvas');
  c.width = c.height = n;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(n / 2, n / 2, 0, n / 2, n / 2, n / 2);
  for (const [at, col] of stops) grd.addColorStop(at, col);
  g.fillStyle = grd;
  g.fillRect(0, 0, n, n);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/* --------------------------------------------------------------- loader -- */

async function loadGLB(loader, url) {
  let buf = null;
  try {
    const r = await fetch(url);
    if (r.ok) {
      const b = await r.arrayBuffer();
      if (b.byteLength > 12 && new DataView(b).getUint32(0, true) === 0x46546C67) buf = b;
    }
  } catch { /* fall through to the sidecar */ }
  if (!buf) {
    const r = await fetch(`${url}.txt`);
    if (!r.ok) throw new Error(`neither ${url} nor its base64 sidecar could be fetched`);
    const bin = atob((await r.text()).trim());
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    buf = bytes.buffer;
  }
  return loader.parseAsync(buf, url.slice(0, url.lastIndexOf('/') + 1));
}

/** Re-orient a model into the forward model's body frame and scale it to size. */
function alignToBodyFrame(obj, spanKm) {
  const box = new THREE.Box3().setFromObject(obj);
  const size = new THREE.Vector3(); box.getSize(size);
  const centre = new THREE.Vector3(); box.getCenter(centre);

  const ext = [
    { axis: new THREE.Vector3(1, 0, 0), len: size.x },
    { axis: new THREE.Vector3(0, 1, 0), len: size.y },
    { axis: new THREE.Vector3(0, 0, 1), len: size.z },
  ].sort((a, b) => b.len - a.len);

  const wing = ext[0].axis;
  const normal = ext[2].axis;
  const spin = new THREE.Vector3().crossVectors(wing, normal).normalize();
  const y = new THREE.Vector3().crossVectors(spin, wing).normalize();
  const m = new THREE.Matrix4().makeBasis(wing, y, spin).transpose();

  const inner = new THREE.Group();
  obj.position.sub(centre);
  inner.add(obj);
  const outer = new THREE.Group();
  outer.add(inner);
  inner.applyMatrix4(m);
  outer.scale.setScalar(spanKm / Math.max(ext[0].len, 1e-9));
  return { group: outer, spanKm };
}

/* ============================================================== the scene = */

export class OrbitScene {
  constructor(canvas, labelHost, opts = {}) {
    this.canvas = canvas;
    this.onPick = opts.onPick || (() => {});
    this.onSelect = opts.onSelect || (() => {});
    this.catalogue = opts.catalogue || [];
    this.mode = 'orbit';
    this.origin = new THREE.Vector3();       // ECI point the scene is drawn about
    this.selected = null;                     // catalogue entry
    this.modelRoot = null;
    this.parts = [];
    this.annotationObjects = [];
    this.specFrac = 0;
    this.clockRate = 1;
    this.simDate = new Date();

    /* ---------------------------------------------------------- renderer */
    this.renderer = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: false,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: true,
      logarithmicDepthBuffer: true,          // half a metre to one AU, one buffer
    });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    this.scene = new THREE.Scene();
    this.scene.background = makeStarSky();

    this.camera = new THREE.PerspectiveCamera(38, 16 / 9, 0.0004, 4.0e6);
    this.camera.position.set(0, 4200, 17000);

    /* -------------------------------------------------------------- Earth */
    const tl = new THREE.TextureLoader();
    const day = tl.load(`${TEX}earth_atmos_2048.jpg`);
    day.colorSpace = THREE.SRGBColorSpace;
    day.anisotropy = 8;
    const lights = tl.load(`${TEX}earth_lights_1024.jpg`);
    lights.colorSpace = THREE.SRGBColorSpace;

    this.sunUnit = new THREE.Vector3(1, 0, 0);

    this.earthMat = new THREE.MeshStandardMaterial({
      map: day,
      normalMap: tl.load(`${TEX}earth_normal_2048.jpg`),
      normalScale: new THREE.Vector2(0.7, 0.7),
      roughnessMap: tl.load(`${TEX}earth_specular_2048.jpg`),
      roughness: 1.0,
      metalness: 0.0,
      emissiveMap: lights,
      emissive: new THREE.Color(0xffc081),
      emissiveIntensity: 0.9,
    });
    this.earthMat.onBeforeCompile = (sh) => {
      sh.uniforms.uSunDir = { value: this.sunUnit };
      sh.vertexShader = sh.vertexShader
        .replace('#include <common>', '#include <common>\nvarying vec3 vWN;')
        .replace('#include <begin_vertex>',
          '#include <begin_vertex>\nvWN = normalize(mat3(modelMatrix) * objectNormal);');
      sh.fragmentShader = sh.fragmentShader
        .replace('#include <common>', '#include <common>\nuniform vec3 uSunDir;\nvarying vec3 vWN;')
        .replace('#include <emissivemap_fragment>', `#include <emissivemap_fragment>
          totalEmissiveRadiance *= smoothstep(0.06, -0.2, dot(normalize(vWN), normalize(uSunDir)));`);
    };

    this.earth = new THREE.Group();
    this.earthMesh = new THREE.Mesh(
      new THREE.SphereGeometry(EARTH_R_KM, 192, 112), this.earthMat);
    this.earth.add(this.earthMesh);

    this.clouds = new THREE.Mesh(
      new THREE.SphereGeometry(EARTH_R_KM + 12, 128, 76),
      new THREE.MeshStandardMaterial({
        color: 0xffffff, alphaMap: tl.load(`${TEX}earth_clouds_1024.jpg`),
        transparent: true, opacity: 0.6, roughness: 1, metalness: 0,
        depthWrite: false,
      }));
    this.earth.add(this.clouds);

    // A real limb is a thin bright line, not a thick blue halo. The shell is
    // 100 km deep against a 6371 km globe, and it brightens only where the
    // line of sight actually grazes it.
    this.atmo = new THREE.Mesh(
      new THREE.SphereGeometry(EARTH_R_KM + 110, 160, 96),
      new THREE.ShaderMaterial({
        transparent: true, side: THREE.BackSide, depthWrite: false,
        blending: THREE.AdditiveBlending,
        uniforms: { uSunDir: { value: this.sunUnit } },
        vertexShader: `
          varying vec3 vN; varying vec3 vV;
          void main(){
            vN = normalize(mat3(modelMatrix) * normal);
            vec4 wp = modelMatrix * vec4(position, 1.0);
            vV = normalize(cameraPosition - wp.xyz);
            gl_Position = projectionMatrix * viewMatrix * wp;
          }`,
        fragmentShader: `
          uniform vec3 uSunDir; varying vec3 vN; varying vec3 vV;
          void main(){
            float rim = pow(1.0 - abs(dot(vN, vV)), 9.0);
            float lit = max(dot(normalize(vN), normalize(uSunDir)), 0.0);
            vec3 col = mix(vec3(0.33,0.52,0.86), vec3(1.0,0.84,0.62), pow(lit,3.0));
            gl_FragColor = vec4(col, rim * pow(lit, 0.85) * 0.85);
          }`,
      }));
    this.earth.add(this.atmo);
    this.scene.add(this.earth);

    /* ------------------------------------------------------------- light */
    this.sunLight = new THREE.DirectionalLight(0xfff6ea, 3.0);
    this.sunLight.castShadow = true;
    const sh = this.sunLight.shadow;
    sh.mapSize.set(2048, 2048);
    sh.camera.near = 0.002;
    sh.camera.far = 2.0;
    sh.camera.left = sh.camera.bottom = -0.03;
    sh.camera.right = sh.camera.top = 0.03;
    sh.bias = -0.00004;
    sh.normalBias = 0.00005;
    this.sunTarget = new THREE.Object3D();
    this.scene.add(this.sunLight, this.sunTarget);
    this.sunLight.target = this.sunTarget;

    this.scene.add(new THREE.AmbientLight(0x101c2c, 0.85));

    this.sunAnchor = new THREE.Object3D();
    this.scene.add(this.sunAnchor);
    const flare = new Lensflare();
    flare.addElement(new LensflareElement(radialTexture([
      [0, 'rgba(255,255,255,1)'], [0.1, 'rgba(255,250,238,0.95)'],
      [0.35, 'rgba(255,228,180,0.22)'], [1, 'rgba(255,214,150,0)'],
    ]), 440, 0, new THREE.Color(0xfff2e0)));
    const ghost = radialTexture([
      [0, 'rgba(255,232,196,0.4)'], [0.7, 'rgba(255,196,146,0.1)'],
      [1, 'rgba(255,186,128,0)'],
    ]);
    flare.addElement(new LensflareElement(ghost, 52, 0.45));
    flare.addElement(new LensflareElement(ghost, 86, 0.65));
    this.sunAnchor.add(flare);

    /* ---------------------------------- the target, nested as R(t) composes */
    this.rockGroup = new THREE.Group();
    this.mountGroup = new THREE.Group();
    this.mountGroup.rotation.x = -Math.PI / 2;
    this.spinGroup = new THREE.Group();
    this.mountGroup.add(this.spinGroup);
    this.rockGroup.add(this.mountGroup);
    this.scene.add(this.rockGroup);

    /* ------------------------------------------------- markers and tracks */
    this.markerTex = radialTexture([
      [0, 'rgba(255,255,255,1)'], [0.25, 'rgba(255,190,120,0.85)'],
      [1, 'rgba(255,170,90,0)'],
    ]);
    this.markers = new THREE.Group();
    this.tracks = new THREE.Group();
    this.scene.add(this.markers, this.tracks);

    /* ------------------------------------------------------------ labels */
    this.css = new CSS2DRenderer();
    this.css.domElement.className = 'scene-labels';
    labelHost.appendChild(this.css.domElement);

    this.controls = new OrbitControls(this.camera, this.css.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.06;
    this.controls.minDistance = 0.006;        // 6 m
    this.controls.maxDistance = 420000;
    this.controls.zoomSpeed = 1.1;
    this.controls.rotateSpeed = 0.55;

    /* -------------------------------------------------------------- post */
    const size = new THREE.Vector2(1, 1);
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.outline = new OutlinePass(size, this.scene, this.camera);
    this.outline.edgeStrength = 3.0;
    this.outline.edgeGlow = 0.2;
    this.outline.visibleEdgeColor.set('#ffb066');
    this.outline.hiddenEdgeColor.set('#3a2a17');
    this.composer.addPass(this.outline);
    this.bloom = new UnrealBloomPass(size, 0.26, 0.55, 0.92);
    this.composer.addPass(this.bloom);
    this.grade = new ShaderPass({
      uniforms: { tDiffuse: { value: null }, uGrain: { value: 0.018 }, uTime: { value: 0 } },
      vertexShader: `varying vec2 vUv;
        void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `
        uniform sampler2D tDiffuse; uniform float uGrain; uniform float uTime;
        varying vec2 vUv;
        float hash(vec2 p){ p = fract(p * vec2(443.8975, 397.2973)); p += dot(p, p.yx + 19.19); return fract((p.x + p.y) * p.x); }
        void main(){
          vec4 c = texture2D(tDiffuse, vUv);
          vec2 d = vUv - 0.5;
          c.rgb *= mix(0.80, 1.0, smoothstep(0.78, 0.18, dot(d, d) * 2.0));
          c.rgb += (hash(vUv + uTime) - 0.5) * uGrain;
          gl_FragColor = c;
        }`,
    });
    this.composer.addPass(this.grade);
    this.composer.addPass(new OutputPass());

    /* ----------------------------------------------------------- picking */
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.css.domElement.addEventListener('pointerdown', (e) => this.#pick(e));
    this.css.domElement.addEventListener('pointermove', (e) => this.#hover(e));

    this.draco = new DRACOLoader();
    this.draco.setDecoderPath('assets/draco/');
    this.loader = new GLTFLoader();
    this.loader.setDRACOLoader(this.draco);

    this.resize();
    this.#captureEnvironment();
  }

  /* ------------------------------------------------------------ catalogue */

  /** Build a marker and an orbit track for every catalogued object. */
  buildCatalogue(date = new Date()) {
    this.markers.clear();
    this.tracks.clear();
    this.markerFor = new Map();

    for (const s of this.catalogue) {
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: this.markerTex, color: 0xffb066, transparent: true,
        depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending,
        sizeAttenuation: false,
      }));
      sprite.scale.set(0.022, 0.022, 1);
      sprite.userData.sat = s;
      this.markers.add(sprite);

      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'sat-tag';
      el.innerHTML = `<b>${s.name}</b><span>${s.norad}</span>`;
      el.addEventListener('pointerdown', (ev) => {
        ev.stopPropagation();
        this.onSelect(s.id);
      });
      const tag = new CSS2DObject(el);
      sprite.add(tag);

      const pts = orbitTrack(s.satrec, date, 240).map((r) => V(r));
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({
          color: 0xffb066, transparent: true, opacity: 0.16, depthWrite: false }));
      line.userData.sat = s;
      this.tracks.add(line);

      this.markerFor.set(s.id, { sprite, line, tag, el });
    }
  }

  /* --------------------------------------------------------------- models */

  async loadModelFor(sat) {
    const spanKm = (sat.sizeM || 5) / 1000;
    if (sat.procedural) {
      const g = new THREE.Group();
      g.add(this.#buildBoxWing(spanKm));
      this.#swapRoot(g);
      this.#collectParts();
      return { facets: MODELS[sat.procedural](), stats: { exact: true, facets: 10, triangles: 0 } };
    }
    const gltf = await loadGLB(this.loader, sat.model);
    const model = gltf.scene;
    model.traverse((o) => {
      if (!o.isMesh) return;
      o.castShadow = true;
      o.receiveShadow = true;
      const mats = Array.isArray(o.material) ? o.material : [o.material];
      for (const m of mats) {
        if (!m) continue;
        m.envMap = this.env;
        m.envMapIntensity = 1.2;
        m.side = THREE.FrontSide;
        // These meshes predate PBR and arrive matte: anything plainly foil or
        // metal is nudged back, or a spacecraft renders as painted plastic.
        if (m.isMeshStandardMaterial && !m.metalnessMap && m.metalness < 0.05) {
          const c = m.color;
          const lum = 0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b;
          if (c.r > c.b * 1.25 && lum > 0.22) { m.metalness = 0.92; m.roughness = Math.min(m.roughness, 0.42); }
          else if (lum > 0.5) { m.metalness = 0.6; m.roughness = Math.min(m.roughness, 0.38); }
          else { m.metalness = 0.25; m.roughness = Math.min(m.roughness, 0.7); }
        }
      }
    });
    const aligned = alignToBodyFrame(model, spanKm);
    this.#swapRoot(aligned.group);
    const { facets, stats } = extractFacets(this.modelRoot);
    this.#collectParts();
    return { facets, stats };
  }

  #buildBoxWing(spanKm) {
    const u = spanKm / 0.3;            // the box-wing is ~300 mm across
    const g = new THREE.Group();
    const bus = new THREE.Mesh(
      new THREE.BoxGeometry(0.06 * u, 0.085 * u, 0.11 * u, 3, 4, 5),
      new THREE.MeshStandardMaterial({
        color: 0xcdd6e2, metalness: 0.95, roughness: 0.3,
        envMap: this.env, envMapIntensity: 1.3, flatShading: true }));
    bus.castShadow = bus.receiveShadow = true;
    g.add(bus);
    const front = new THREE.MeshPhysicalMaterial({
      color: 0x0b2550, metalness: 0.3, roughness: 0.05, clearcoat: 1,
      envMap: this.env, envMapIntensity: 2.0, emissive: 0x4ea0ff, emissiveIntensity: 0 });
    const back = new THREE.MeshStandardMaterial({ color: 0xcfcabb, roughness: 0.95 });
    this.panelMat = front;
    for (const sign of [1, -1]) {
      const plate = new THREE.Mesh(
        new THREE.BoxGeometry(0.15 * u, 0.003 * u, 0.096 * u),
        [back, back, front, back, back, back]);
      plate.position.x = sign * 0.109 * u;
      plate.castShadow = plate.receiveShadow = true;
      g.add(plate);
    }
    return g;
  }

  #swapRoot(group) {
    if (this.modelRoot) {
      this.spinGroup.remove(this.modelRoot);
      this.modelRoot.traverse((o) => {
        if (o.isMesh) {
          o.geometry?.dispose?.();
          const mm = Array.isArray(o.material) ? o.material : [o.material];
          mm.forEach((m) => m?.dispose?.());
        }
      });
    }
    this.modelRoot = group;
    this.spinGroup.add(group);
  }

  #collectParts() {
    this.parts = [];
    this.modelRoot?.traverse((o) => { if (o.isMesh) this.parts.push(o); });
    this.hovered = null; this.picked = null;
    this.outline.selectedObjects = [];
  }

  /* ------------------------------------------------------------ annotations */

  buildAnnotations(spec) {
    for (const a of this.annotationObjects) {
      a.obj.parent?.remove(a.obj);
      a.dot?.parent?.remove(a.dot);
    }
    this.annotationObjects = [];
    if (!spec?.annotations?.length) return;
    const span = (spec.sizeM || 5) / 1000;

    for (const a of spec.annotations) {
      const p = new THREE.Vector3(a.at[0] * span, a.at[1] * span, a.at[2] * span);
      const dot = new THREE.Sprite(new THREE.SpriteMaterial({
        map: this.markerTex, color: 0xffc98a, transparent: true,
        depthTest: false, depthWrite: false, blending: THREE.AdditiveBlending,
        sizeAttenuation: false }));
      dot.scale.set(0.012, 0.012, 1);
      dot.position.copy(p);
      this.spinGroup.add(dot);

      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'scene-label is-part';
      el.textContent = a.label;
      el.addEventListener('pointerdown', (e) => {
        e.stopPropagation();
        this.onPick({ kind: 'annotation', data: a, target: spec });
        for (const x of this.annotationObjects) {
          x.el.classList.toggle('is-selected', x.data === a);
        }
      });
      const obj = new CSS2DObject(el);
      obj.position.copy(p);
      this.spinGroup.add(obj);
      this.annotationObjects.push({ obj, el, dot, data: a });
    }
  }

  /* ---------------------------------------------------------- environment */

  #captureEnvironment() {
    const rt = new THREE.WebGLCubeRenderTarget(256, { type: THREE.HalfFloatType });
    const cam = new THREE.CubeCamera(1, 4.0e6, rt);
    cam.position.set(0, 0, 0);
    const hide = [this.markers, this.tracks];
    const was = hide.map((o) => o.visible);
    hide.forEach((o) => { o.visible = false; });
    if (this.modelRoot) this.modelRoot.visible = false;
    cam.update(this.renderer, this.scene);
    hide.forEach((o, i) => { o.visible = was[i]; });
    if (this.modelRoot) this.modelRoot.visible = true;

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    const prev = this.env;
    this.env = pmrem.fromCubemap(rt.texture).texture;
    pmrem.dispose(); rt.dispose(); prev?.dispose?.();
    this.scene.environment = this.env;
  }

  /* ------------------------------------------------------------- picking */

  #ray(ev) {
    const r = this.canvas.getBoundingClientRect();
    this.pointer.x = ((ev.clientX - r.left) / r.width) * 2 - 1;
    this.pointer.y = -((ev.clientY - r.top) / r.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    return this.raycaster;
  }

  #hover(ev) {
    const ray = this.#ray(ev);
    if (this.mode === 'orbit') {
      const hit = ray.intersectObjects(this.markers.children, false)[0];
      this.css.domElement.style.cursor = hit ? 'pointer' : '';
      return;
    }
    if (!this.parts.length) return;
    const hit = ray.intersectObjects(this.parts, false)[0];
    const next = hit ? hit.object : null;
    if (next !== this.hovered) {
      this.hovered = next;
      this.css.domElement.style.cursor = next ? 'pointer' : '';
      this.outline.selectedObjects = [this.picked, this.hovered].filter(Boolean);
    }
  }

  #pick(ev) {
    const ray = this.#ray(ev);
    if (this.mode === 'orbit') {
      const hit = ray.intersectObjects(this.markers.children, false)[0];
      if (hit) this.onSelect(hit.object.userData.sat.id);
      return;
    }
    if (!this.parts.length) return;
    const h = ray.intersectObjects(this.parts, false)[0];
    if (!h) { this.picked = null; this.outline.selectedObjects = []; return; }
    this.picked = h.object;
    this.outline.selectedObjects = [this.picked];
    const mat = Array.isArray(h.object.material)
      ? h.object.material[h.face?.materialIndex ?? 0] : h.object.material;
    this.onPick({
      kind: 'mesh',
      meshName: h.object.name || '(unnamed mesh)',
      materialName: mat?.name || '(unnamed material)',
      rangeM: h.distance * 1000,
    });
  }

  /* --------------------------------------------------------- the origin -- */

  /**
   * Move the rendering origin without moving the picture.
   *
   * A four-metre spacecraft seven thousand kilometres from Earth's centre has
   * no float precision left if it is drawn there. Everything is therefore
   * drawn relative to whatever is being looked at, and the camera is shifted
   * by the same amount so the view does not jump.
   */
  setOrigin(eci, keepView = true) {
    const next = V(eci);
    const delta = next.clone().sub(this.origin);
    if (delta.lengthSq() === 0) return;
    // Re-basing between two fixed points must not move the picture, so the
    // camera is shifted to compensate. Tracking a moving object is the
    // opposite case: the camera is already stored relative to the origin, so
    // leaving it alone is exactly what makes it ride along at 7.5 km/s.
    if (keepView) {
      this.camera.position.sub(delta);
      this.controls.target.sub(delta);
    }
    this.origin.copy(next);
  }

  /* ------------------------------------------------------------- framing */

  /** Pull back until Earth and every track are in frame. */
  showOrbitView(ms = 1400) {
    this.mode = 'orbit';
    this.setOrigin({ x: 0, y: 0, z: 0 });
    this.markers.visible = true;
    this.tracks.visible = true;
    if (this.modelRoot) this.modelRoot.visible = false;
    this.#setAnnotationsVisible(false);
    this.#fly(new THREE.Vector3(1800, 9000, 24000), new THREE.Vector3(0, 0, 0), ms);
  }

  /** Close on one object until it fills the frame, with Earth behind it. */
  showInspectView(eci, sizeKm, ms = 1600) {
    this.mode = 'inspect';
    this.setOrigin(eci);
    this.markers.visible = false;
    this.tracks.visible = false;
    if (this.modelRoot) this.modelRoot.visible = true;
    this.#setAnnotationsVisible(true);
    const d = Math.max(sizeKm * 2.4, 0.010);
    const dir = new THREE.Vector3(-0.55, 0.3, 0.78).normalize();
    this.#fly(dir.multiplyScalar(d), new THREE.Vector3(0, 0, 0), ms);
  }

  #setAnnotationsVisible(on) {
    for (const a of this.annotationObjects) {
      a.obj.visible = on;
      a.dot.visible = on;
    }
  }

  /** Queue a camera move; it is advanced by the render loop, not its own rAF. */
  #fly(toPos, toTarget, ms) {
    this.flight = {
      fromPos: this.camera.position.clone(),
      fromTgt: this.controls.target.clone(),
      toPos: toPos.clone(),
      toTgt: toTarget.clone(),
      t0: performance.now(),
      ms: Math.max(1, ms),
    };
  }

  #advanceFlight() {
    const f = this.flight;
    if (!f) return;
    const u = Math.min(1, (performance.now() - f.t0) / f.ms);
    const e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;

    // Interpolate the logarithm of the distance: a fly from 24,000 km down to
    // fifteen metres has to cover six decades, and a linear lerp spends almost
    // all of its time in the last metre.
    const d0 = Math.max(f.fromPos.length(), 1e-6);
    const d1 = Math.max(f.toPos.length(), 1e-6);
    const d = Math.exp(THREE.MathUtils.lerp(Math.log(d0), Math.log(d1), e));
    const dir = f.fromPos.clone().normalize()
      .lerp(f.toPos.clone().normalize(), e).normalize();
    this.camera.position.copy(dir.multiplyScalar(d));
    this.controls.target.lerpVectors(f.fromTgt, f.toTgt, e);
    if (u >= 1) this.flight = null;
  }

  /* ------------------------------------------------------------- setters */

  setLabelsVisible(on) { this.css.domElement.style.display = on ? '' : 'none'; }
  setTracksVisible(on) { this.tracks.visible = on && this.mode === 'orbit'; }
  setAutoRotate(on) { this.controls.autoRotate = on; }

  setExploded(x) {
    const k = THREE.MathUtils.clamp(x, 0, 1);
    if (!this.parts.length) return;
    const span = this.selected ? (this.selected.sizeM || 5) / 1000 : 0.005;
    const c = new THREE.Vector3();
    for (const m of this.parts) {
      if (!m.userData._home) m.userData._home = m.position.clone();
      const box = new THREE.Box3().setFromObject(m);
      box.getCenter(c);
      c.normalize().multiplyScalar(k * span * 0.5);
      m.position.copy(m.userData._home).add(c);
    }
  }

  /* --------------------------------------------------------------- frame */

  /**
   * One frame.
   *
   * `now` is the simulated wall clock; `state` is the instantaneous attitude
   * and photometry from the engine. Everything else here is geometry computed
   * from real elements, not animation.
   */
  update(now, state, dt) {
    const sunEci = sunVectorEci(now);
    this.sunUnit.set(sunEci.x, sunEci.y, sunEci.z);

    // Earth's orientation is sidereal time, so the terminator is real.
    this.earth.rotation.y = gmstAt(now);
    this.earth.position.copy(this.origin).multiplyScalar(-1);
    this.clouds.rotation.y += dt * 0.000006;

    this.sunAnchor.position.copy(this.sunUnit).multiplyScalar(AU_KM)
      .sub(this.origin);

    // Markers and tracks follow the real propagation.
    if (this.mode === 'orbit' && this.markerFor) {
      for (const s of this.catalogue) {
        const m = this.markerFor.get(s.id);
        if (!m) continue;
        const pv = propagate(s.satrec, now);
        if (!pv) { m.sprite.visible = false; continue; }
        m.sprite.visible = true;
        m.sprite.position.copy(V(pv.r)).sub(this.origin);
        const lit = !inEclipse(pv.r, sunEci);
        m.sprite.material.color.set(lit ? 0xffb066 : 0x4d5b70);
        m.el.classList.toggle('is-dark', !lit);
        m.line.position.copy(this.origin).multiplyScalar(-1);
      }
    }

    // The target itself.
    if (this.selected) {
      const pv = propagate(this.selected.satrec, now);
      if (pv) {
        this.targetEci = pv.r;
        this.targetVel = pv.velocity || pv.v;
        if (this.mode === 'inspect') this.setOrigin(pv.r, false);
        this.rockGroup.position.copy(V(pv.r)).sub(this.origin);
      }
    }

    if (state) {
      this.spinGroup.rotation.z = state.phi;
      this.rockGroup.rotation.x = state.eps;
      const sf = THREE.MathUtils.clamp(state.specFrac, 0, 1);
      this.specFrac += (sf - this.specFrac) * Math.min(1, dt * 12);
      this.bloom.strength = 0.22 + 0.5 * Math.pow(this.specFrac, 1.6);
      if (this.panelMat) this.panelMat.emissiveIntensity = 1.4 * Math.pow(this.specFrac, 1.8);
    }

    // The sun lamp is parked just off the target so the shadow frustum, which
    // is metres across, actually contains it.
    const tgt = this.rockGroup.position;
    this.sunTarget.position.copy(tgt);
    this.sunLight.position.copy(tgt).addScaledVector(this.sunUnit, 0.5);

    this.#advanceFlight();
    this.grade.uniforms.uTime.value = (performance.now() * 0.001) % 1.0;
    this.controls.update();
    this.css.render(this.scene, this.camera);
    this.composer.render();
  }

  /** Live geometry for the readout: all of it measured off the real frame. */
  observation(now) {
    if (!this.selected || !this.targetEci) return null;
    const sunEci = sunVectorEci(now);
    const camEci = this.camera.position.clone().add(this.origin);
    return {
      eci: this.targetEci,
      vel: this.targetVel,
      sunEci,
      eclipsed: inEclipse(this.targetEci, sunEci),
      phaseDeg: phaseAngleDeg(this.targetEci, camEci, sunEci),
      rangeKm: camEci.distanceTo(V(this.targetEci)),
    };
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const w = Math.max(280, r.width), h = Math.max(220, r.height);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.outline.setSize(w, h);
    this.css.setSize(w, h);
  }
}
