"""Box-wing forward model for the SDC-001 testbed.

Generates the reflected flux of a faceted target that spins about Axis A while
the cradle carrying Axis A rocks through a triangular sweep about Axis B.

Frames
------
Body frame   spin axis is body +z. Facet normals are fixed in this frame.
Lab frame    +z is the viewing direction (target -> camera). The Axis B shaft
             lies along lab +x. At zero rock the spin axis points along lab +y,
             i.e. broadside to the observer, which is where the spin modulation
             is deepest. Rocking about lab +x swings the spin axis in the y-z
             plane, toward and away from the line of sight -- this is what
             imprints the Axis B period on the light-curve envelope.

Composition of the body-to-lab rotation at time t:

    R(t) = Rx(eps(t)) . M0 . Rz(phi(t))

with phi the spin phase, eps the rock angle, and M0 = Rx(-90 deg) the fixed
mounting rotation that carries body +z onto lab +y.

Reflection
----------
Each facet contributes a Lambertian term and a Blinn-Phong specular lobe, both
gated on the facet being simultaneously lit and visible. The target is
unresolved, so only facet normals and areas matter -- facet positions, and
therefore self-shadowing, are deliberately not modelled (Rev B section 1.3:
this is a parameterised box-wing, not a shape solver).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from config import PHASE_ANGLE_DEG

# --------------------------------------------------------------------- facets


@dataclass(frozen=True)
class Facet:
    """One reflecting surface, fixed in the body frame."""

    normal: tuple[float, float, float]
    area: float          # relative units; absolute scale is degenerate
    rho_d: float         # Lambertian albedo
    rho_s: float         # specular lobe strength
    shininess: float     # Blinn-Phong exponent; larger = narrower glint
    label: str = ""
    is_panel: bool = False   # scaled by the fitted panel-area parameter


def _unit(v):
    v = np.asarray(v, dtype=float)
    return tuple(v / np.linalg.norm(v))


# Bus: aluminised mylar tape, deliberately creased (Rev B section 9.1). Broad,
# multi-lobed return -- mostly diffuse with a wide, weak specular component.
_BUS_KW = dict(rho_d=0.35, rho_s=0.25, shininess=8.0)

# Panel front: blue transparent facing over the printed substrate. Flat and
# strongly directional -- this is what makes the glints.
_PANEL_FRONT_KW = dict(rho_d=0.06, rho_s=2.6, shininess=180.0)

# Panel back: bare printed PLA, near-uniform diffuser.
_PANEL_BACK_KW = dict(rho_d=0.22, rho_s=0.0, shininess=1.0)


def _bus_facets(w=60.0, d=85.0, h=110.0) -> list[Facet]:
    """Six faces of the printed bus, dimensions in mm.

    w and d must differ. A square cross-section would make the bus invariant
    under a 90 deg rotation about the spin axis, putting the dominant
    periodogram power at 4/P_A instead of 2/P_A and turning a two-way harmonic
    ambiguity into a four-way one -- an artefact of the model, not of the
    physics being demonstrated.
    """
    faces = [
        ((+1, 0, 0), d * h, "bus+x"),
        ((-1, 0, 0), d * h, "bus-x"),
        ((0, +1, 0), w * h, "bus+y"),
        ((0, -1, 0), w * h, "bus-y"),
        ((0, 0, +1), w * d, "bus+z"),
        ((0, 0, -1), w * d, "bus-z"),
    ]
    return [Facet(n, a, label=lab, **_BUS_KW) for n, a, lab in faces]


def model_a_facets(panel_area: float = 4000.0) -> list[Facet]:
    """Model A -- symmetric box-wing, two opposed panels.

    The facet set is invariant under a 180 deg rotation about the spin axis:
    the +x wing maps onto the -x wing and the bus maps onto itself. The light
    curve is therefore *exactly* periodic at P_A/2, and no amount of photometry
    can distinguish P_A from P_A/2. That is the physics, not a defect in the
    analysis -- Rev B section 9.2 makes Model B the diagnostic case.
    """
    facets = _bus_facets()
    facets += [
        Facet((0, +1, 0), panel_area, label="panelA_front",
              is_panel=True, **_PANEL_FRONT_KW),
        Facet((0, -1, 0), panel_area, label="panelA_back",
              is_panel=True, **_PANEL_BACK_KW),
        Facet((0, -1, 0), panel_area, label="panelB_front",
              is_panel=True, **_PANEL_FRONT_KW),
        Facet((0, +1, 0), panel_area, label="panelB_back",
              is_panel=True, **_PANEL_BACK_KW),
    ]
    return facets


def model_b_facets(panel_area: float = 4000.0) -> list[Facet]:
    """Model B -- single panel plus an offset dish.

    Breaking the 180 deg symmetry is the whole point: the fold test can now
    tell P_A from P_A/2, because consecutive half-cycles differ.
    """
    facets = _bus_facets()
    facets += [
        Facet((0, +1, 0), panel_area, label="panel_front",
              is_panel=True, **_PANEL_FRONT_KW),
        Facet((0, -1, 0), panel_area, label="panel_back",
              is_panel=True, **_PANEL_BACK_KW),
    ]
    # Offset dish: a broad, tilted, moderately shiny cap. Its normal is not
    # related to the panel normal by any symmetry of the bus.
    facets += [
        Facet(_unit((0.62, 0.55, 0.56)), 0.55 * panel_area, rho_d=0.30,
              rho_s=0.9, shininess=30.0, label="dish"),
    ]
    return facets


MODELS = {"A": model_a_facets, "B": model_b_facets}

# Panel area is degenerate with lamp brightness and distance, so photometry can
# only ever constrain it *relative* to the bus. The inversion therefore fits a
# panel-to-bus area ratio, and these are the ratios of the nominal facet sets.
NOMINAL_PANEL_AREA = 4000.0


def area_ratio_of(model: str, panel_area: float = NOMINAL_PANEL_AREA) -> float:
    """One-sided panel area divided by total bus area, for a facet set."""
    facets = MODELS[model](panel_area=panel_area)
    bus = sum(f.area for f in facets if not f.is_panel and f.label.startswith("bus"))
    panel = sum(f.area for f in facets if f.is_panel and f.label.endswith("front"))
    return panel / bus


def panel_area_for_ratio(model: str, ratio: float) -> float:
    """Inverse of area_ratio_of: the panel_area giving the requested ratio."""
    return NOMINAL_PANEL_AREA * ratio / area_ratio_of(model)


# ------------------------------------------------------------------ kinematics


def triangle_wave(x: np.ndarray) -> np.ndarray:
    """Unit triangle wave on [-1, 1], period 1, zero and rising at x = 0.

    Matches the firmware, which starts at the centre of the sweep and travels
    positive until it reaches the limit.
    """
    return 4.0 * np.abs(((x - 0.25) % 1.0) - 0.5) - 1.0


def body_to_lab(normals: np.ndarray, phi: np.ndarray, eps: np.ndarray) -> np.ndarray:
    """Rotate body-frame normals into the lab frame at every sample time.

    normals : (F, 3) body-frame unit normals
    phi     : (T,) spin phase in radians
    eps     : (T,) rock angle in radians
    returns : (T, F, 3)
    """
    nx, ny, nz = normals[:, 0], normals[:, 1], normals[:, 2]

    cphi = np.cos(phi)[:, None]
    sphi = np.sin(phi)[:, None]

    # Rz(phi) about the body spin axis
    ux = nx[None, :] * cphi - ny[None, :] * sphi
    uy = nx[None, :] * sphi + ny[None, :] * cphi
    uz = np.broadcast_to(nz[None, :], ux.shape)

    # M0 = Rx(-90 deg):  (x, y, z) -> (x, z, -y)
    vx, vy, vz = ux, uz, -uy

    # Rx(eps) -- the cradle rock about the lab x axis
    ceps = np.cos(eps)[:, None]
    seps = np.sin(eps)[:, None]

    return np.stack([vx, vy * ceps - vz * seps, vy * seps + vz * ceps], axis=-1)


# -------------------------------------------------------------------- geometry


def viewing_geometry(phase_angle_deg: float = PHASE_ANGLE_DEG):
    """Unit vectors to the camera and to the lamp, separated by the phase angle."""
    a = np.radians(phase_angle_deg)
    o = np.array([0.0, 0.0, 1.0])                 # target -> camera
    s = np.array([np.sin(a), 0.0, np.cos(a)])     # target -> lamp
    return o, s


# ------------------------------------------------------------------ parameters


@dataclass
class LightCurveParams:
    """Free parameters of the forward model.

    scale and offset absorb the unknown lamp brightness, aperture gain and
    stray-light pedestal; they are nuisance parameters, not results.
    """

    p_spin: float
    p_prec: float
    cone_deg: float
    panel_area: float = 4000.0
    spin_phase0: float = 0.0      # radians
    prec_phase0: float = 0.0      # fraction of a precession cycle, [0, 1)
    rho_d: float = 1.0            # multiplies every diffuse term
    scale: float = 1.0
    offset: float = 0.0

    def to_vector(self) -> np.ndarray:
        return np.array([
            self.p_spin, self.p_prec, self.cone_deg, self.panel_area,
            self.spin_phase0, self.prec_phase0, self.rho_d,
            self.scale, self.offset,
        ])

    @classmethod
    def from_vector(cls, v) -> "LightCurveParams":
        return cls(*map(float, v))

    NAMES = (
        "p_spin", "p_prec", "cone_deg", "panel_area",
        "spin_phase0", "prec_phase0", "rho_d", "scale", "offset",
    )


# ---------------------------------------------------------------- light curve


def light_curve(t: np.ndarray, params: LightCurveParams,
                facets: list[Facet], phase_angle_deg: float = PHASE_ANGLE_DEG,
                return_components: bool = False):
    """Noise-free reflected flux at times ``t`` (seconds)."""
    t = np.asarray(t, dtype=float)
    o, s = viewing_geometry(phase_angle_deg)

    h = s + o
    h /= np.linalg.norm(h)

    phi = 2.0 * np.pi * t / params.p_spin + params.spin_phase0
    eps = np.radians(params.cone_deg) * triangle_wave(
        t / params.p_prec + params.prec_phase0
    )

    normals = np.array([f.normal for f in facets], dtype=float)
    n = body_to_lab(normals, phi, eps)            # (T, F, 3)

    mu_i = n @ s          # (T, F) cos incidence
    mu_e = n @ o          # (T, F) cos emission
    lit = (mu_i > 0.0) & (mu_e > 0.0)

    mu_i = np.where(lit, mu_i, 0.0)
    mu_e = np.where(lit, mu_e, 0.0)

    area = np.array([f.area for f in facets], dtype=float)
    is_panel = np.array([f.is_panel for f in facets], dtype=bool)
    # Panel facets carry the fitted area; bus facets keep their nominal size.
    # panel_area is expressed relative to the value baked into the facet set.
    area = np.where(is_panel, area * (params.panel_area / 4000.0), area)

    rho_d = np.array([f.rho_d for f in facets], dtype=float) * params.rho_d
    rho_s = np.array([f.rho_s for f in facets], dtype=float)
    shine = np.array([f.shininess for f in facets], dtype=float)

    diffuse = (area * rho_d / np.pi) * mu_i * mu_e

    nh = np.where(lit, np.clip(n @ h, 0.0, None), 0.0)
    specular = (area * rho_s) * (nh ** shine) * mu_i

    flux = params.scale * (diffuse.sum(axis=1) + specular.sum(axis=1)) + params.offset

    if return_components:
        return flux, params.scale * diffuse.sum(axis=1), params.scale * specular.sum(axis=1)
    return flux


# ------------------------------------------------------------------ synthesis


def synthesize(duration: float, fps: float, params: LightCurveParams,
               model: str = "A", noise_frac: float = 0.02,
               stray_light_frac: float = 0.015, drift_frac: float = 0.0,
               phase_angle_deg: float = PHASE_ANGLE_DEG,
               seed: int | None = 0):
    """Sampled, noisy light curve plus the clean curve it was drawn from.

    noise_frac       Gaussian sigma as a fraction of the mean clean flux, with a
                     shot-noise-like sqrt(flux) weighting on top.
    stray_light_frac Additive pedestal as a fraction of the peak clean flux.
                     Rev B section 8.3 requires this below 2 %.
    drift_frac       Fractional linear droop over the recording, standing in for
                     an unheatsunk COB losing output.
    """
    rng = np.random.default_rng(seed)

    n = int(round(duration * fps))
    t = np.arange(n) / fps

    facets = MODELS[model](panel_area=params.panel_area)
    clean = light_curve(t, params, facets, phase_angle_deg)

    pedestal = stray_light_frac * clean.max()
    signal = clean + pedestal

    if drift_frac:
        signal = signal * (1.0 - drift_frac * t / max(t[-1], 1e-9))

    sigma = noise_frac * signal.mean() * np.sqrt(
        np.clip(signal, 0.0, None) / max(signal.mean(), 1e-12)
    )
    sigma = np.clip(sigma, 1e-6 * signal.mean(), None)
    observed = signal + rng.normal(0.0, sigma)

    return t, observed, sigma, clean


def default_params(model: str = "A", truth=None) -> LightCurveParams:
    """Commanded truth expressed as forward-model parameters."""
    from config import TRUTH

    truth = truth or TRUTH
    return LightCurveParams(
        p_spin=truth.p_spin,
        p_prec=truth.p_prec,
        cone_deg=truth.cone_deg,
        panel_area=4000.0,
        spin_phase0=0.37,      # arbitrary, unknown to the fit
        prec_phase0=0.11,
        rho_d=1.0,
        scale=1.0,
        offset=0.0,
    )
