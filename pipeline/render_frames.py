"""Render a synthetic light curve into actual image frames, and encode a .mov.

This is what lets stages 3-6 be built and tested before any hardware exists. A
light curve alone tests the period search but not the photometry: it cannot
catch an aperture that clips the target at some attitudes, a background annulus
that overlaps the source, or a centroid that wanders into the glint. Rendering
frames whose *total* flux is the known light curve, then measuring them back,
tests the whole chain and has a right answer at every step.

Deliberately included because each one breaks something downstream:

  photon noise   Noise enters where it physically does -- Poisson in the pixels
                 -- instead of being added to the light curve by hand.
  stray light    A pedestal the background annulus has to remove. Rev B section
                 8.3 requires it below 2 % of peak.
  gamma          iPhone video is gamma-encoded, so recorded values are not
                 linear in flux (Rev B section 8.4). Period survives any
                 monotonic transform; amplitude does not.
  attitude shape The target is drawn as a rotating bar, not a fixed blob, so a
                 too-small aperture loses flux at some rotation phases and
                 injects a spurious signal at the spin frequency.
  centroid drift Slow tripod settling, to exercise the drift diagnostic.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from frames import find_ffmpeg


def target_pattern(shape, cx, cy, phi, bus=(7.0, 11.0), panel=(22.0, 4.5),
                   blur=1.4):
    """Unit-sum intensity pattern of the target at spin phase ``phi``.

    A rectangular bus with two panels extending either side, rotated in the
    image plane. The pattern changes shape with phase while its integral stays
    at one, so all the flux variation in a rendered sequence comes from the
    light curve and none of it from the drawing.
    """
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    x = xx - cx
    y = yy - cy
    c, s = np.cos(phi), np.sin(phi)
    xr = x * c + y * s
    yr = -x * s + y * c

    m = ((np.abs(xr) <= bus[0]) & (np.abs(yr) <= bus[1])).astype(float)
    m += 0.85 * ((np.abs(xr) <= panel[0]) & (np.abs(yr) <= panel[1])).astype(float)

    m = gaussian_filter(m, blur)
    tot = m.sum()
    return m / tot if tot > 0 else m


def render_sequence(t, flux, outdir, size=(160, 120), phi=None,
                    peak_photons=9.0e4, stray_frac=0.015, read_noise=1.2,
                    gamma=2.2, full_well=None, headroom=0.88, drift_px=0.6,
                    pattern="f%06d.png", seed=0, progress_every=600):
    """Write a PNG sequence whose per-frame total flux follows ``flux``.

    peak_photons  photons in the target at peak flux; sets the shot-noise floor.
    full_well     photons mapping to pixel value 1.0 before gamma. Leave as None
                  to set it automatically so the brightest pixel in the whole
                  sequence lands at ``headroom`` of full scale -- which is the
                  Rev B section 8.4 instruction, "the longest exposure that does
                  not clip the glint peak", performed rather than assumed.
                  Getting this wrong is quiet and expensive: a target sitting at
                  1 % of full scale is quantised to a handful of 8-bit levels
                  and the photometry is then mostly rounding error, with no
                  symptom other than a disappointing residual.
    gamma         encoding exponent; 1.0 for a linear (scientific) camera.
    """
    rng = np.random.default_rng(seed)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob(pattern.replace("%06d", "*")):
        old.unlink()

    w, h = size
    shape = (h, w)
    f = np.asarray(flux, dtype=float)
    fn = f / f.max()

    if phi is None:
        phi = np.zeros_like(f)

    cx0, cy0 = w / 2.0, h / 2.0
    n = f.size
    paths = []

    bkg_per_px = stray_frac * peak_photons / (w * h) * 8.0

    if full_well is None:
        # Brightest pixel over a sweep of phases at peak flux. The pattern is
        # renormalised to unit sum at every phase, so its maximum varies with
        # attitude and the worst case has to be searched for, not guessed.
        pat_max = max(target_pattern(shape, cx0, cy0, a).max()
                      for a in np.linspace(0.0, np.pi, 12, endpoint=False))
        full_well = (pat_max * peak_photons + bkg_per_px) / headroom

    for i in range(n):
        # Slow settle, largest at the start, asymptoting to zero.
        d = drift_px * np.exp(-3.0 * i / n)
        cx = cx0 + d
        cy = cy0 - 0.6 * d

        pat = target_pattern(shape, cx, cy, phi[i])
        lam = pat * fn[i] * peak_photons + bkg_per_px

        counts = rng.poisson(np.clip(lam, 0, None)).astype(float)
        counts += rng.normal(0.0, read_noise, shape)

        img = np.clip(counts / full_well, 0.0, 1.0)
        if gamma != 1.0:
            img = img ** (1.0 / gamma)

        p = outdir / (pattern % (i + 1))
        Image.fromarray((img * 255.0 + 0.5).astype(np.uint8), mode="L").save(p)
        paths.append(p)

        if progress_every and i % progress_every == 0:
            print("    rendering {}/{}".format(i, n), end="\r")

    if progress_every:
        print(" " * 44, end="\r")
    return paths


def encode_video(framedir, out_path, fps=60.0, pattern="f%06d.png", crf=14):
    """Encode the rendered frames into a constant-frame-rate .mov.

    -r on both input and output plus H.264 in a QuickTime container is the
    closest stand-in available here for what the phone produces. Encoding and
    then re-extracting means stage 2 is exercised against a real container
    rather than assumed to work.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
           "-framerate", str(fps), "-i", str(Path(framedir) / pattern),
           "-c:v", "libx264", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-r", str(fps), str(out_path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
