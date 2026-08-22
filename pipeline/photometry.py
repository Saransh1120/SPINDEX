"""Stage 3: aperture photometry on the extracted frames.

Rev B section 10.2 specifies a fixed circular aperture enclosing the full target
envelope at all attitudes, plus an annular background region; aperture sum minus
scaled background is the raw flux.

"Fixed" is the important word and it is not laziness. A per-frame recentred
aperture tracks the target's brightness centroid, which moves toward whichever
facet is glinting. The aperture would then follow the signal, changing how much
of the target it encloses in a way correlated with the rotation -- injecting a
spurious modulation at exactly the frequency being measured. A fixed aperture
large enough to hold the target at every attitude cannot do that. recentre= is
available for diagnosing drift, and warns when you use it.

photutils and OpenCV are the tools the spec names. Neither is installed here and
neither is needed: a circular aperture sum and a sigma-clipped annulus median are
a few lines of NumPy. The Aperture geometry mirrors the photutils API, so
swapping the implementation later changes nothing upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    from PIL import Image
except ImportError as e:
    raise RuntimeError("Pillow is required to read frames: pip install pillow") from e


# Rec.709 luma weights. The target is lit by a ~5000 K COB and the panels are
# blue-tinted, so channel weighting is not neutral -- but any fixed linear
# combination of channels preserves periodicity, which is what stages 4-6 need.
LUMA = np.array([0.2126, 0.7152, 0.0722])


def load_frame(path, channel: str = "luma", gamma: float = 1.0) -> np.ndarray:
    """Read one frame as a float array in [0, 1].

    gamma > 1 undoes the camera's gamma encoding, returning values linear in
    incident flux. This is the miniature of the Rev B section 8.4 mitigation:
    the printed step wedge in the field of view gives the real response curve,
    and this is where its inverse is applied. It must happen per pixel, before
    the aperture sum -- linearising the summed flux afterwards is not the same
    operation and does not undo the distortion.
    """
    a = np.asarray(Image.open(path), dtype=np.float64)
    if a.ndim == 3:
        a = a[:, :, :3]
        if channel == "luma":
            a = a @ LUMA
        elif channel in ("r", "g", "b"):
            a = a[:, :, "rgb".index(channel)]
        else:
            raise ValueError("unknown channel " + repr(channel))
    a = a / 255.0
    return a ** gamma if gamma != 1.0 else a


def stack_median(paths, max_frames: int = 60, channel: str = "luma",
                 gamma: float = 1.0) -> np.ndarray:
    """Median of frames spread across the whole recording, to locate the target.

    Spread rather than the first N, so the median sees the target at many
    attitudes and is not biased by whatever it was doing at the start.
    """
    paths = list(paths)
    idx = np.unique(np.linspace(0, len(paths) - 1,
                                min(max_frames, len(paths))).astype(int))
    return np.median([load_frame(paths[i], channel, gamma) for i in idx], axis=0)


@dataclass
class Aperture:
    """Fixed circular aperture with a concentric background annulus, in pixels."""

    x: float
    y: float
    r: float
    r_in: float
    r_out: float

    def masks(self, shape):
        yy, xx = np.ogrid[:shape[0], :shape[1]]
        d2 = (xx - self.x) ** 2 + (yy - self.y) ** 2
        return d2 <= self.r ** 2, (d2 >= self.r_in ** 2) & (d2 <= self.r_out ** 2)

    def __str__(self):
        return ("centre ({:.1f}, {:.1f}) px, r = {:.1f} px, annulus {:.1f}-{:.1f} px"
                .format(self.x, self.y, self.r, self.r_in, self.r_out))


def find_aperture(stack: np.ndarray, threshold_frac: float = 0.10,
                  pad: float = 1.6, annulus=(1.25, 1.9)) -> Aperture:
    """Size and place the aperture from a median stack.

    Thresholds against the median rather than a low percentile, then keeps only
    the largest connected blob. Both matter: on frames that have not been
    linearised, gamma encoding lifts the stray-light floor towards the target's
    own level, so a naive threshold selects most of the frame and the "target"
    comes out the size of the sensor. Taking the largest connected component
    instead means a hot pixel or a light leak in the corner cannot drag the
    aperture off the target.

    Radius is ``pad`` times the greatest distance from the centroid to a lit
    pixel, so the aperture holds the target envelope at every attitude present
    in the stack.
    """
    from scipy.ndimage import label

    med = float(np.median(stack))
    hi = float(stack.max())
    if hi <= med:
        raise RuntimeError("frame has no contrast; check exposure and that the "
                           "lamp was on")
    mask = stack >= med + threshold_frac * (hi - med)
    if not mask.any():
        raise RuntimeError("no pixels above threshold; check exposure or the "
                           "stray-light floor")

    lab, n = label(mask)
    if n > 1:
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        mask = lab == int(np.argmax(sizes))

    yy, xx = np.nonzero(mask)
    w = stack[yy, xx]
    x0 = float((xx * w).sum() / w.sum())
    y0 = float((yy * w).sum() / w.sum())
    r = float(np.sqrt((xx - x0) ** 2 + (yy - y0) ** 2).max()) * pad

    ap = Aperture(x0, y0, r, r * annulus[0], r * annulus[1])

    h, wd = stack.shape
    if (x0 - ap.r_out < 0 or x0 + ap.r_out >= wd
            or y0 - ap.r_out < 0 or y0 + ap.r_out >= h):
        raise RuntimeError(
            "background annulus (r_out = {:.0f} px, centred at ({:.0f}, {:.0f})) "
            "does not fit in a {}x{} frame. The target is filling too much of "
            "the field: move the camera back, or re-frame so the target spans "
            "roughly a third of the short side.".format(
                ap.r_out, x0, y0, wd, h))
    return ap


def centroid(frame: np.ndarray, ap: Aperture, search_r=None):
    """Flux-weighted centroid inside the aperture. Diagnostic only."""
    yy, xx = np.ogrid[:frame.shape[0], :frame.shape[1]]
    m = (xx - ap.x) ** 2 + (yy - ap.y) ** 2 <= (search_r or ap.r) ** 2
    w = np.clip(frame, 0, None) * m
    tot = w.sum()
    if tot <= 0:
        return ap.x, ap.y
    ys, xs = np.nonzero(m)
    return (float((xs * w[ys, xs]).sum() / tot), float((ys * w[ys, xs]).sum() / tot))


def _clipped_median(v, nsig: float = 3.0, iters: int = 3) -> float:
    v = v[np.isfinite(v)]
    for _ in range(iters):
        if v.size < 8:
            break
        m, s = np.median(v), v.std()
        keep = np.abs(v - m) <= nsig * s
        if keep.all():
            break
        v = v[keep]
    return float(np.median(v)) if v.size else 0.0


@dataclass
class PhotometryResult:
    flux: np.ndarray
    background: np.ndarray
    err: np.ndarray
    cx: np.ndarray
    cy: np.ndarray
    aperture: Aperture
    n_pix: int
    saturated: np.ndarray

    def summary(self) -> str:
        sat = int(self.saturated.sum())
        drift = float(np.hypot(self.cx - self.cx[0], self.cy - self.cy[0]).max())
        out = ["  aperture      : {}".format(self.aperture),
               "  aperture area : {} px".format(self.n_pix),
               "  mean bkg/px   : {:.5f}".format(self.background.mean()),
               "  centroid drift: {:.2f} px (max, from frame 0)".format(drift),
               "  saturated frames: {}".format(sat)]
        if sat:
            out.append("    ! clipping flattens the glint peaks, which is where "
                       "the geometric information lives. Shorten the exposure.")
        return "\n".join(out)


def measure(paths, ap: Aperture, channel: str = "luma", gain: float = 1.0,
            recentre: bool = False, sat_level: float = 0.995,
            gamma: float = 1.0, progress_every: int = 0) -> PhotometryResult:
    """Aperture photometry over a sequence of frames.

    gain is electrons per unit intensity; it sets the scale of the Poisson error
    estimate only, not the flux.
    """
    paths = list(paths)
    n = len(paths)
    flux = np.empty(n)
    bkg = np.empty(n)
    err = np.empty(n)
    cx = np.empty(n)
    cy = np.empty(n)
    sat = np.zeros(n, dtype=bool)

    shape = load_frame(paths[0], channel, gamma).shape
    ap_mask, bg_mask = ap.masks(shape)
    n_pix = int(ap_mask.sum())
    n_bg = int(bg_mask.sum())
    if n_bg < 32:
        raise RuntimeError("background annulus has too few pixels; it is "
                           "probably falling off the frame edge")

    if recentre:
        print("  ! recentre=True: the aperture follows the brightness centroid, "
              "which moves with the glint. Diagnostics only -- do not measure a "
              "light curve this way.")

    for i, p in enumerate(paths):
        f = load_frame(p, channel, gamma)
        if recentre:
            x, y = centroid(f, ap)
            ap_mask, bg_mask = Aperture(x, y, ap.r, ap.r_in, ap.r_out).masks(shape)
        cx[i], cy[i] = centroid(f, ap)

        b = _clipped_median(f[bg_mask])
        flux[i] = f[ap_mask].sum() - b * n_pix
        bkg[i] = b
        # Judged against the linearised equivalent of the clip point, since f
        # has already been gamma-decoded above.
        sat[i] = bool((f[ap_mask] >= sat_level ** gamma).any())

        # Poisson on the source, plus the annulus-median uncertainty propagated
        # through the aperture area.
        var = max(flux[i], 0.0) / gain + n_pix * (1.0 + n_pix / n_bg) * b / gain
        err[i] = np.sqrt(max(var, 1e-30))

        if progress_every and i % progress_every == 0:
            print("    frame {}/{}".format(i, n), end="\r")

    if progress_every:
        print(" " * 44, end="\r")

    return PhotometryResult(flux, bkg, err, cx, cy, ap, n_pix, sat)
