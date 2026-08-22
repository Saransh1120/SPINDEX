"""Live spin-state monitor: webcam in, recovered period on screen.

    python pipeline/live.py                              # webcam 0
    python pipeline/live.py --source data/raw/synthetic_model_B.mov
    python pipeline/live.py --source synthetic            # no camera needed
    python pipeline/live.py --roi 320 240 40 --window 60

The recorded pipeline (run_pipeline.py -> validate.py) is unchanged and remains
the instrument of record. This is a bench aid: it tells you within a few seconds
whether the target is framed, exposed and spinning at roughly the commanded
rate, so you find out before recording 120 s rather than after.

WHY THE TIME BASE IS DIFFERENT HERE
-----------------------------------
The recorded pipeline gets its time base from the frame index, because a locked
60 fps recording is uniform by construction and §4.2 makes the frame rate the
clock. A webcam is not that. It drops frames under load, and USB scheduling
jitters arrival times by milliseconds. Frame index divided by nominal fps is
therefore *wrong* live, and wrong in a way that shifts the recovered period.

So every sample here is stamped with time.perf_counter() at grab time and the
periodogram is fed real timestamps. Lomb-Scargle handles unevenly sampled data
natively -- that is precisely why it is the right tool and why an FFT is not.
The measured cadence and its scatter are shown on screen; if the jitter is a
large fraction of the frame interval, the camera, not the analysis, is the
problem.

THREATS TO A LIVE MEASUREMENT, IN ORDER
---------------------------------------
1. Auto-exposure. It responds to exactly the brightness variations being
   measured and actively flattens them (§8.4). Defeated on startup, verified by
   read-back, and shouted about if the driver refuses.
2. Auto white balance and auto gain, same argument.
3. Saturation. A clipped glint peak is information destroyed, not compressed.
4. Mains-flicker beat. A 50/60 Hz lamp beating against the frame rate puts a
   real-looking peak in the periodogram (§8.1).
"""

from __future__ import annotations

import argparse
import csv
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import period as ps
import photometry as ph

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------- frame sources


class FrameSource:
    """Yields (timestamp, grayscale float frame in [0,1])."""

    name = "base"
    nominal_fps = 30.0

    def read(self):
        raise NotImplementedError

    def close(self):
        pass


def _to_gray(bgr) -> np.ndarray:
    # OpenCV delivers BGR; photometry.LUMA is in RGB order, so reverse.
    return (bgr[:, :, ::-1].astype(np.float64) @ ph.LUMA) / 255.0


def list_devices(max_index=8):
    """Enumerate capture devices so you can find the OBS Virtual Camera index.

    OpenCV exposes no device *names*, so identification is by resolution and by
    what changes when you toggle a source. An OBS Virtual Camera reports the OBS
    canvas size -- 1920x1080 by default -- which is usually distinguishable from
    a built-in webcam at 640x480 or 1280x720. If two devices look alike, stop
    the OBS virtual camera, re-run this, and see which index disappears.
    """
    import cv2

    backends = [("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)]
    if sys.platform != "win32":
        backends = [("ANY", cv2.CAP_ANY)]

    print("Scanning capture devices (this takes a few seconds) ...")
    print("  {:<6}{:<8}{:<14}{:>8}{:>10}   {}".format(
        "index", "backend", "resolution", "fps", "mean", "note"))
    found = 0
    for idx in range(max_index):
        for bname, be in backends:
            cap = cv2.VideoCapture(idx, be)
            if not cap.isOpened():
                cap.release()
                continue
            ok, frame = cap.read()
            if ok and frame is not None:
                h, w = frame.shape[:2]
                fps = cap.get(cv2.CAP_PROP_FPS)
                mean = float(frame.mean())
                note = ""
                if (w, h) in ((1920, 1080), (1280, 720)) and w >= 1280:
                    note = "could be OBS Virtual Camera"
                if mean < 5:
                    note = (note + "; " if note else "") + "nearly black - lens covered or dark"
                print("  {:<6}{:<8}{:<14}{:>8.1f}{:>10.2f}   {}".format(
                    idx, bname, "{}x{}".format(w, h), fps, mean, note))
                found += 1
            cap.release()
    if not found:
        print("  no devices found. If using OBS, click 'Start Virtual Camera' first.")
    return found


def exposure_self_test(cap, cv2, settle=10, n=8):
    """Does the image actually respond to exposure commands?

    Reading CAP_PROP_AUTO_EXPOSURE back is not evidence: drivers report values
    they do not honour, and one on this bench returns -1 for a property it
    silently ignores. The only trustworthy test is to command two different
    exposures and see whether the pixels move. If they do not, auto-exposure is
    in charge, and it will flatten exactly the modulation being measured.
    """
    import numpy as _np

    levels = {}
    for ev in (-9, -5):
        cap.set(cv2.CAP_PROP_EXPOSURE, ev)
        for _ in range(settle):
            cap.read()
        vals = []
        for _ in range(n):
            ok, f = cap.read()
            if ok and f is not None:
                vals.append(float(f.mean()))
        levels[ev] = float(_np.mean(vals)) if vals else float("nan")

    lo, hi = levels[-9], levels[-5]
    if not (_np.isfinite(lo) and _np.isfinite(hi)) or lo <= 0:
        return False, "exposure self-test inconclusive (no frames)"
    ratio = hi / lo
    if max(lo, hi) < 5.0:
        return False, ("exposure self-test inconclusive: scene is nearly black "
                       "(mean {:.1f}/255). Light the scene and re-run.".format(max(lo, hi)))
    if ratio > 1.25 or ratio < 0.8:
        return True, "manual exposure CONFIRMED (levels {:.1f} -> {:.1f})".format(lo, hi)
    return False, ("MANUAL EXPOSURE NOT WORKING: levels {:.1f} -> {:.1f} for a "
                   "4-stop command. Auto-exposure is flattening the signal you "
                   "are measuring. Lock exposure upstream (in the phone camera "
                   "app or OBS) before trusting anything below.".format(lo, hi))


class WebcamSource(FrameSource):
    """Live camera, with every automatic behaviour we can reach turned off."""

    name = "webcam"

    def __init__(self, index=0, width=640, height=480, fps=30.0,
                 exposure=None, backend=None):
        import cv2
        self.cv2 = cv2

        if backend is None and sys.platform == "win32":
            backend = cv2.CAP_DSHOW      # MSMF ignores most property writes
        self.cap = cv2.VideoCapture(index, backend) if backend else cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise SystemExit(
                "Could not open camera {}. Close anything else using it "
                "(Teams, Zoom, the Camera app) and check privacy settings."
                .format(index))

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.warnings = self._defeat_automatics(exposure)
        self.nominal_fps = self.cap.get(cv2.CAP_PROP_FPS) or fps

        for _ in range(5):               # let the pipeline settle
            self.cap.read()

    def _defeat_automatics(self, exposure):
        """Turn off AE/AWB/AGC and verify. Returns a list of complaints.

        Property support is driver-dependent and set() returning True does not
        mean the driver honoured it, so every write is read back. A camera that
        refuses is not a fatal error -- it is a caveat the operator has to see,
        because the light curve will be flattened in a way nothing downstream
        can detect.
        """
        cv2 = self.cv2
        warn = []

        # DSHOW: 0.25 == manual, 0.75 == auto. V4L2: 1 == manual, 3 == auto.
        got = None
        for val in (0.25, 1, 0):
            self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, val)
            got = self.cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
            if got in (0.25, 1, 0):
                break
        if got not in (0.25, 1, 0):
            warn.append("AUTO-EXPOSURE could not be disabled (reads {}). The "
                        "camera is actively flattening the modulation you are "
                        "trying to measure.".format(got))

        if exposure is not None:
            self.cap.set(cv2.CAP_PROP_EXPOSURE, exposure)

        if self.cap.set(cv2.CAP_PROP_AUTO_WB, 0):
            if self.cap.get(cv2.CAP_PROP_AUTO_WB) not in (0, 0.0):
                warn.append("auto white balance may still be active")
        else:
            warn.append("auto white balance not controllable on this driver")

        return warn

    def read(self):
        ok, frame = self.cap.read()
        t = time.perf_counter()          # stamped as close to the grab as we can
        if not ok or frame is None:
            return None, None
        return t, _to_gray(frame)

    def close(self):
        self.cap.release()


class VideoFileSource(FrameSource):
    """A recorded clip, played at its own frame rate. The fallback path.

    ``realtime`` paces playback to the file's fps so the whole live path --
    buffering, threading, periodogram cadence -- is exercised exactly as it will
    be with a camera. Turn it off to grind through a file as fast as possible.
    """

    name = "file"

    def __init__(self, path, realtime=True, loop=False):
        import cv2
        self.cv2 = cv2
        self.path = str(path)
        self.cap = cv2.VideoCapture(self.path)
        if not self.cap.isOpened():
            raise SystemExit("Could not open video file: {}".format(path))
        self.nominal_fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.realtime = realtime
        self.loop = loop
        self._t0 = None
        self._n = 0

    def read(self):
        ok, frame = self.cap.read()
        if not ok or frame is None:
            if self.loop:
                self.cap.set(self.cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
                if not ok:
                    return None, None
            else:
                return None, None

        now = time.perf_counter()
        if self._t0 is None:
            self._t0 = now
        if self.realtime:
            due = self._t0 + self._n / self.nominal_fps
            if due > now:
                time.sleep(due - now)
                now = time.perf_counter()
        self._n += 1
        return now, _to_gray(frame)

    def close(self):
        self.cap.release()


class SyntheticSource(FrameSource):
    """Frames generated on the fly from the forward model, with known truth.

    Lets the live path be exercised with no camera and no file, and -- because
    the commanded period is known -- lets the on-screen number be checked
    against an answer instead of merely looked at.
    """

    name = "synthetic"

    def __init__(self, model="B", fps=30.0, size=(320, 240), peak_photons=6e4,
                 gamma=2.2, seed=0):
        import config
        import forward_model as fm
        import render_frames as rf

        self.rf = rf
        self.truth = config.TRUTH
        self.params = fm.default_params(model, self.truth)
        self.facets = fm.MODELS[model](panel_area=self.params.panel_area)
        self.fm = fm
        self.nominal_fps = fps
        self.size = size
        self.peak_photons = peak_photons
        self.gamma = gamma
        self.rng = np.random.default_rng(seed)
        self.t0 = None
        self._n = 0

        w, h = size
        pat_max = max(rf.target_pattern((h, w), w / 2, h / 2, a).max()
                      for a in np.linspace(0, np.pi, 12, endpoint=False))
        self.full_well = pat_max * peak_photons / 0.88
        # Peak flux over a full precession cycle, so per-frame flux can be
        # normalised without knowing the future.
        tt = np.linspace(0, self.truth.p_prec, 600)
        self.fmax = float(fm.light_curve(tt, self.params, self.facets).max())

    def read(self):
        now = time.perf_counter()
        if self.t0 is None:
            self.t0 = now
        due = self.t0 + self._n / self.nominal_fps
        if due > now:
            time.sleep(due - now)
            now = time.perf_counter()
        t = now - self.t0
        self._n += 1

        w, h = self.size
        flux = float(self.fm.light_curve(np.array([t]), self.params,
                                         self.facets)[0]) / self.fmax
        phi = 2 * np.pi * t / self.params.p_spin + self.params.spin_phase0
        pat = self.rf.target_pattern((h, w), w / 2, h / 2, phi)
        lam = pat * max(flux, 0.0) * self.peak_photons + 3.0
        img = self.rng.poisson(lam) / self.full_well
        img = np.clip(img, 0, 1) ** (1.0 / self.gamma)
        return now, img


def make_source(args) -> FrameSource:
    if args.source == "synthetic":
        return SyntheticSource(model=args.model, fps=args.fps)
    if args.source is None or str(args.source).isdigit():
        return WebcamSource(int(args.source or 0), args.width, args.height,
                            args.fps, exposure=args.exposure)
    return VideoFileSource(args.source, realtime=not args.fast, loop=args.loop)


# ------------------------------------------------------------- live photometry


def build_aperture(frames, roi=None, shrink_to_fit=True):
    """Fixed aperture from a short warm-up stack, or from an explicit ROI.

    Fixed for the same reason as the recorded pipeline: an aperture that
    recentres each frame follows the glint and injects a signal at the spin
    frequency. Unlike the recorded pipeline this must not raise -- a live tool
    that dies because the annulus is two pixels off the edge is useless at the
    bench -- so an oversized aperture is shrunk, with a warning, instead.
    """
    notes = []
    stack = np.median(frames, axis=0)
    h, w = stack.shape

    if roi is not None:
        x, y, r = roi
        ap = ph.Aperture(float(x), float(y), float(r), r * 1.25, r * 1.9)
    else:
        try:
            ap = ph.find_aperture(stack)
        except RuntimeError as e:
            notes.append("auto aperture failed ({}); using a centred default"
                         .format(e.args[0][:60]))
            r = 0.18 * min(h, w)
            ap = ph.Aperture(w / 2, h / 2, r, r * 1.25, r * 1.9)

    if shrink_to_fit:
        room = min(ap.x, ap.y, w - 1 - ap.x, h - 1 - ap.y)
        if ap.r_out > room:
            scale = room / ap.r_out * 0.98
            ap = ph.Aperture(ap.x, ap.y, ap.r * scale, ap.r_in * scale,
                             ap.r_out * scale)
            notes.append("aperture shrunk x{:.2f} to keep the annulus on frame; "
                         "move the camera back for a cleaner background"
                         .format(scale))
    return ap, stack, notes


class LivePhotometer:
    """Per-frame aperture photometry with masks precomputed once.

    The aperture is fixed, so the masks never change and the per-frame cost is
    two boolean-indexed reductions. That is what keeps this able to run at
    camera rate without dropping frames.
    """

    def __init__(self, aperture: ph.Aperture, shape, sat_level=0.99):
        self.ap = aperture
        self.ap_mask, self.bg_mask = aperture.masks(shape)
        self.n_pix = int(self.ap_mask.sum())
        self.n_bg = int(self.bg_mask.sum())
        self.sat_level = sat_level
        if self.n_bg < 32:
            raise SystemExit("background annulus has under 32 pixels; give a "
                             "smaller --roi radius")

    def measure(self, frame):
        ap_vals = frame[self.ap_mask]
        bg = float(np.median(frame[self.bg_mask]))
        flux = float(ap_vals.sum()) - bg * self.n_pix
        return flux, bg, bool((ap_vals >= self.sat_level).any())


# ------------------------------------------------------------------ shared state


@dataclass
class Solution:
    t_computed: float = 0.0
    n_samples: int = 0
    span: float = 0.0
    f_peak: float = float("nan")
    p_peak: float = float("nan")
    p_adopted: float = float("nan")
    harmonic: float = float("nan")
    power: float = float("nan")
    fap: float = float("nan")
    freq: np.ndarray | None = None
    spec: np.ndarray | None = None
    p_env: float = float("nan")      # envelope (Axis B) period, if resolvable
    f_env: float = float("nan")
    note: str = ""


class State:
    """Ring buffer plus the latest solution, shared across three threads."""

    def __init__(self, window_s=60.0, max_rate=120.0):
        self.lock = threading.Lock()
        n = int(window_s * max_rate) + 64
        self.t = deque(maxlen=n)
        self.f = deque(maxlen=n)
        self.bg_hist = deque(maxlen=n)
        self.sat_hist = deque(maxlen=n)
        self.window_s = window_s
        self.frame = None
        self.solution = Solution()
        self.n_frames = 0
        self.n_sat = 0
        self.bg = 0.0
        self.running = True
        self.notes = []
        self.ap_mask = None
        self.featureset = None          # latest features.FeatureSet
        self.notifications = []         # NotificationDecision, newest last
        self.engine = None              # importance.NotificationEngine
        self.expect_period = None       # commanded period, if the user gave one

        # Rolling activity log shown in the dashboard's log panel. Bounded, so a
        # long run cannot grow it without limit, and written under the same lock
        # as everything else so the display never reads a half-appended line.
        self.log_lines = deque(maxlen=500)
        self.t_zero = time.perf_counter()

    def log(self, msg):
        """Append one timestamped line to the activity log."""
        with self.lock:
            self.log_lines.append(
                (time.perf_counter() - self.t_zero, str(msg)))

    def log_tail(self, n=40):
        with self.lock:
            return list(self.log_lines)[-n:]

    def append(self, t, flux, frame, sat, bg):
        with self.lock:
            self.t.append(t)
            self.f.append(flux)
            self.bg_hist.append(bg)
            self.sat_hist.append(bool(sat))
            self.frame = frame
            self.n_frames += 1
            self.n_sat += bool(sat)
            self.bg = bg
            # Trim anything older than the analysis window.
            cutoff = t - self.window_s
            while self.t and self.t[0] < cutoff:
                self.t.popleft()
                self.f.popleft()
                if self.bg_hist:
                    self.bg_hist.popleft()
                if self.sat_hist:
                    self.sat_hist.popleft()

    def snapshot(self):
        with self.lock:
            return np.fromiter(self.t, float), np.fromiter(self.f, float)


def false_alarm_probability(power, n):
    """Baluev-style FAP for the strongest peak of a normalised periodogram.

    Rough, but its job is only to grey out the display when the peak is not
    yet significant, so an order of magnitude is enough.
    """
    if not np.isfinite(power) or n < 8:
        return 1.0
    m = max(n / 2.0, 2.0)          # crude independent-frequency count
    return float(np.clip(1.0 - (1.0 - (1.0 - power) ** ((n - 3) / 2.0)) ** m, 0, 1))


# ----------------------------------------------------------------- period worker


def solve_period(t, y, f_min, f_max, do_walk=True, min_span=4.0,
                 f_split=0.5) -> Solution:
    """One Lomb-Scargle solve on the current buffer.

    The spectrum is split at ``f_split`` and the headline period comes from the
    band ABOVE it. This matters: the precession envelope is the strongest thing
    in the periodogram by a wide margin -- roughly 0.30 power against 0.13 for
    the spin -- so taking the global maximum makes a spin-state monitor
    confidently report the rock period instead. The offline search() splits the
    same way for the same reason. The envelope is still reported, just not as
    the headline.

    Runs on real timestamps, not a frame index. The harmonic walk needs evenly
    spaced data (it uses boxcar filters), so for that step alone the curve is
    interpolated onto a uniform grid at the median observed cadence. The
    periodogram itself never is -- interpolating before a Lomb-Scargle would
    throw away the one property that makes it the right tool.
    """
    s = Solution(t_computed=time.perf_counter(), n_samples=t.size)
    if t.size < 32:
        s.note = "filling buffer"
        return s

    s.span = float(t[-1] - t[0])
    if s.span < min_span:
        s.note = "need {:.0f} s of data".format(min_span)
        return s

    tt = t - t[0]
    yy = y - y.mean()
    if not np.any(np.abs(yy) > 0):
        s.note = "flat signal"
        return s
    yy = yy / (yy.std() or 1.0)

    # The buffer only supports frequencies with a couple of cycles in it.
    f_lo = max(f_min, 2.0 / s.span)
    freq, power = ps.gls(tt, yy, None, f_min=f_lo, f_max=f_max, oversample=8.0)
    if freq.size < 4:
        s.note = "frequency range empty"
        return s

    s.freq, s.spec = freq, power

    hi = freq > f_split
    lo = ~hi
    if np.any(lo) and np.any(power[lo] > 0):
        j = int(np.argmax(np.where(lo, power, -1.0)))
        s.f_env = float(ps.refine_peak(tt, yy, None, float(freq[j])))
        if np.isfinite(s.f_env) and s.f_env > 0:
            s.p_env = 1.0 / s.f_env

    if not np.any(hi):
        s.note = "no frequencies above the {:.2f} Hz band split".format(f_split)
        return s
    k = int(np.argmax(np.where(hi, power, -1.0)))
    s.power = float(power[k])
    s.f_peak = float(ps.refine_peak(tt, yy, None, float(freq[k])))
    if not np.isfinite(s.f_peak) or s.f_peak <= 0:
        s.note = "no peak"
        return s
    s.p_peak = 1.0 / s.f_peak
    s.fap = false_alarm_probability(s.power, t.size)

    s.p_adopted = s.p_peak
    s.harmonic = 1.0
    if do_walk and s.span > 6.0 * s.p_peak:
        dt = float(np.median(np.diff(tt)))
        if dt > 0:
            grid = np.arange(0.0, tt[-1], dt)
            # The walk must get a POSITIVE, unit-mean curve. Its first step is
            # flatten(), which divides by a boxcar average to strip the
            # envelope; on the zero-mean standardised series used for the
            # periodogram that divisor oscillates through zero and the fold
            # test silently returns garbage -- it reported x1 for a target the
            # offline pipeline resolves to x4. Offline always passes unit-mean
            # flux, so match that here.
            yg = np.interp(grid, tt, y)
            yg = yg - yg.min() + 1e-9
            yg = yg / yg.mean()
            try:
                # span/8, not span/4. The fold test compares two consecutive
                # half-cycles; at span/4 the doubled period leaves only ~4
                # cycles per half, built from nearly disjoint data, and noise
                # alone reads as "these halves differ" -- measured: a true 64 s
                # period doubled to 128 s with 9 cycles in the buffer. Eight
                # cycles is the floor at which the comparison means anything.
                p_adopted, _, _ = ps.walk_subharmonics(
                    grid, yg, None, s.f_peak, max_doublings=3,
                    max_period=s.span / 8.0)
                s.p_adopted = float(p_adopted)
                s.harmonic = s.p_adopted * s.f_peak
            except Exception as e:
                s.note = "walk skipped ({})".format(type(e).__name__)
    else:
        s.note = "span too short to resolve P vs P/2"
    return s


def period_thread(state: State, args):
    """Solution, features and notifications, all off the capture path.

    Everything expensive runs here rather than in the display callback: a slow
    periodogram must never stall the camera or the redraw. Each stage is guarded
    separately, so a failure in feature extraction still leaves you a working
    period readout instead of a dead window.
    """
    import anomaly
    import features as featmod
    import importance as imp

    engine = imp.NotificationEngine(
        imp.PRESETS.get(getattr(args, "policy", "bench"), imp.PRESETS["bench"]))
    detector = anomaly.DriftDetector(warmup=6)
    with state.lock:
        state.engine = engine
    state.log("analysis thread up, policy '{}'".format(engine.policy.name))

    n_solve = 0
    while state.running:
        t, y = state.snapshot()
        try:
            sol = solve_period(t, y, args.fmin, args.fmax,
                               do_walk=not args.no_walk, f_split=args.split)
        except Exception as e:                       # never kill the display
            sol = Solution(note="solver error: {}".format(e))
            state.log("SOLVER ERROR {}".format(type(e).__name__))

        n_solve += 1
        if np.isfinite(sol.p_adopted):
            state.log("solve #{} P={:.4f}s x{:g} pow={:.2f} span={:.0f}s".format(
                n_solve, sol.p_adopted, sol.harmonic, sol.power, sol.span))
        else:
            state.log("solve #{}: no period ({})".format(
                n_solve, sol.note or "acquiring"))

        fs = None
        fired = []
        if not getattr(args, "no_features", False) and t.size > 64:
            try:
                with state.lock:
                    bg = np.fromiter(state.bg_hist, float)
                    sat = np.fromiter(state.sat_hist, bool)
                n = min(t.size, bg.size, sat.size)
                fs = featmod.extract(t[-n:], y[-n:], fmin=args.fmin,
                                     fmax=args.fmax,
                                     background=bg[-n:] if n else None,
                                     saturated=sat[-n:] if n else None,
                                     solve=sol, f_split=args.split)
                state.log("feat {} SNR={:.1f} depth={:.3f} Q={:.1f}".format(
                    len(fs.to_dict()), fs.snr, fs.modulation_depth, fs.peak_q))

                evs = anomaly.detect(fs, detector, source="live")
                if evs:
                    state.log("anom {}: {}".format(
                        len(evs), ",".join(sorted({e.kind for e in evs}))[:34]))
                engine.observe_absent([e.kind for e in evs])
                for d in engine.submit_all(evs):
                    if d.fired:
                        fired.append(d)
                        state.log("NOTIFY [{}] {} imp={:.2f}".format(
                            d.event.severity, d.event.kind, d.importance))
            except Exception as e:
                state.log("FEATURE ERROR {}: {}".format(type(e).__name__, e)[:78])
                sol.note = (sol.note + "; " if sol.note else "") + \
                    "feature error: {}".format(type(e).__name__)

        with state.lock:
            state.solution = sol
            if fs is not None:
                state.featureset = fs
            if fired:
                state.notifications.extend(fired)
                del state.notifications[:-40]
        time.sleep(args.solve_every)


# --------------------------------------------------------------- capture thread


def capture_thread(state: State, source: FrameSource, photometer_holder,
                   args, writer=None):
    while state.running:
        t, frame = source.read()
        if frame is None:
            state.running = False
            break
        pm = photometer_holder[0]
        flux, bg, sat = pm.measure(frame)
        state.append(t, flux, frame, sat, bg)

        # Throughput heartbeat. Logged every 150 frames rather than every frame:
        # the log is a human-readable trace, not a per-sample record, and the
        # per-sample record already exists in --record.
        n = state.n_frames
        if n % 150 == 0:
            with state.lock:
                nsat = state.n_sat
            state.log("cap {} frames flux={:.4g} sat={}".format(n, flux, nsat))
        if writer is not None:
            writer.writerow(["{:.6f}".format(t), "{:.8g}".format(flux),
                             "{:.8g}".format(bg), int(sat)])


# ------------------------------------------------------------------- display
#
# The dashboard lives in dashboard.py. It is the only part of this file that
# imports pyplot, so a headless run never touches a GUI toolkit.


def run_display(state, source, aperture, args):
    import dashboard
    dashboard.run_display(state, source, aperture, args)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=None,
                    help="camera index (default 0), a video path, or 'synthetic'")
    ap.add_argument("--model", default="B", help="target model for --source synthetic")
    ap.add_argument("--list-devices", action="store_true",
                    help="enumerate capture devices and exit")
    ap.add_argument("--backend", choices=["dshow", "msmf", "any"],
                    help="force a capture backend (Windows default: dshow)")
    ap.add_argument("--no-self-test", action="store_true",
                    help="skip the startup exposure-response check")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=float, default=30.0, help="requested camera rate")
    ap.add_argument("--exposure", type=float, default=None,
                    help="manual exposure value; driver-specific units")
    ap.add_argument("--roi", type=float, nargs=3, metavar=("X", "Y", "R"),
                    help="fixed aperture in pixels instead of auto-placement")
    ap.add_argument("--window", type=float, default=60.0,
                    help="analysis window in seconds")
    ap.add_argument("--warmup", type=float, default=2.0,
                    help="seconds of frames used to place the aperture")
    ap.add_argument("--fmin", type=float, default=None,
                    help="lowest frequency searched. Default: 3 cycles in the "
                         "analysis window, i.e. 3/--window")
    ap.add_argument("--fmax", type=float, default=None,
                    help="highest frequency searched. Default: 0.4 x frame rate, "
                         "safely below Nyquist")
    ap.add_argument("--split", type=float, default=0.0,
                    help="Hz dividing an envelope band from the spin band; the "
                         "headline period is then taken from ABOVE it. Only "
                         "meaningful for the two-axis rig, where the precession "
                         "envelope dominates the periodogram. 0 = off (default), "
                         "which is correct for a single rotating target.")
    ap.add_argument("--solve-every", type=float, default=1.0)
    ap.add_argument("--redraw-every", type=float, default=0.1)
    ap.add_argument("--policy", choices=["bench", "unattended", "import"],
                    default="bench",
                    help="notification policy; 'unattended' is stricter")
    ap.add_argument("--no-features", action="store_true",
                    help="disable feature extraction and notifications")
    ap.add_argument("--expect", type=float,
                    help="commanded period in seconds, shown against the recovery")
    ap.add_argument("--no-walk", action="store_true",
                    help="report the raw LS peak without harmonic resolution")
    ap.add_argument("--record", metavar="CSV",
                    help="append every sample to a CSV for offline re-analysis")
    ap.add_argument("--fast", action="store_true",
                    help="file source: ignore fps pacing")
    ap.add_argument("--loop", action="store_true", help="file source: loop")
    ap.add_argument("--headless", type=float, metavar="SECONDS",
                    help="no window; run this long and print the solution")
    args = ap.parse_args(argv)

    if args.list_devices:
        list_devices()
        return 0

    source = make_source(args)
    print("source: {}  nominal {:.1f} fps".format(source.name, source.nominal_fps))

    # Derive the search range from what the data can actually support, rather
    # than from constants that happened to suit the designed 1.85 s rig. A fixed
    # 0.05-5 Hz window silently excludes a target turning once a minute, and
    # silently aliases a desk fan. Both failures look like "no period found".
    fps = source.nominal_fps or 30.0
    if args.fmin is None:
        args.fmin = 3.0 / args.window          # need ~3 cycles in the buffer
    nyquist = 0.5 * fps
    if args.fmax is None:
        # Deliberately conservative. Opening this up to near-Nyquist admits
        # codec and sensor noise at high frequency, which produces confident
        # spurious peaks -- measured: a 60 fps clip whose true period is 1.85 s
        # reported 0.13 s once fmax was raised to 24 Hz. Widening the search
        # does not find more signal, it finds more noise. Raise it explicitly
        # (--fmax) only if you know the target turns faster than 0.2 s.
        args.fmax = min(5.0, 0.8 * nyquist)
    print("search range: {:.4f} - {:.3f} Hz   (periods {:.3f} s to {:.1f} s)"
          .format(args.fmin, args.fmax, 1.0 / args.fmax, 1.0 / args.fmin))
    print("  Nyquist {:.1f} Hz at {:.0f} fps. A target turning faster than "
          "{:.2f} s/rev is".format(nyquist, fps, 1.0 / args.fmax))
    print("  outside this range: raise --fmax, but stay under {:.1f} Hz or it "
          "will alias".format(0.8 * nyquist))
    print("  to a WRONG period rather than to no period.")
    if args.split > 0:
        print("  band split at {:.2f} Hz: headline taken from above it "
              "(two-axis rig)".format(args.split))

    state = State(window_s=args.window,
                  max_rate=max(source.nominal_fps * 1.5, 60.0))
    state.expect_period = args.expect
    state.notes = list(getattr(source, "warnings", []))
    for w in state.notes:
        print("  ! {}".format(w))

    n_warm = max(int(args.warmup * source.nominal_fps), 8)
    print("warming up ({} frames) ...".format(n_warm))
    warm = []
    while len(warm) < n_warm:
        t, f = source.read()
        if f is None:
            raise SystemExit("source ended during warm-up")
        warm.append(f)

    aperture, stack, notes = build_aperture(warm, roi=args.roi)
    state.notes += notes
    for w in notes:
        print("  ! {}".format(w))
    print("aperture: {}".format(aperture))
    state.log("source '{}' at {:.1f} fps nominal".format(source.name,
                                                         source.nominal_fps))
    state.log("aperture {}".format(aperture))

    photometer = LivePhotometer(aperture, warm[0].shape)
    state.ap_mask = photometer.ap_mask       # dashboard histograms this region
    print("  {} px in aperture, {} px in annulus".format(
        photometer.n_pix, photometer.n_bg))
    state.log("{} px aperture / {} px annulus".format(
        photometer.n_pix, photometer.n_bg))
    state.log("search {:.4f}-{:.3f} Hz  window {:.0f}s".format(
        args.fmin, args.fmax, args.window))
    for w in state.notes:
        state.log("WARN {}".format(w)[:78])

    t_now = time.perf_counter()
    for i, f in enumerate(warm):
        flux, bg, sat = photometer.measure(f)
        state.append(t_now - (len(warm) - i) / source.nominal_fps, flux, f, sat, bg)

    fh = writer = None
    if args.record:
        pth = Path(args.record)
        pth.parent.mkdir(parents=True, exist_ok=True)
        fh = open(pth, "w", newline="")
        writer = csv.writer(fh)
        writer.writerow(["t_perf_s", "flux", "background", "saturated"])
        print("recording samples -> {}".format(pth))

    cap = threading.Thread(target=capture_thread, daemon=True,
                           args=(state, source, [photometer], args, writer))
    sol = threading.Thread(target=period_thread, daemon=True, args=(state, args))
    cap.start()
    sol.start()

    try:
        if args.headless is not None:
            _run_headless(state, args)
        else:
            run_display(state, source, aperture, args)
    except KeyboardInterrupt:
        pass
    finally:
        state.running = False
        time.sleep(0.2)
        source.close()
        if fh:
            fh.close()

    _final_report(state, source, args)
    return 0


def _run_headless(state: State, args):
    """No window: useful over SSH, in CI, and for testing the live path."""
    t_end = time.perf_counter() + args.headless
    last = 0.0
    while state.running and time.perf_counter() < t_end:
        time.sleep(0.25)
        with state.lock:
            s = state.solution
        if s.t_computed > last and np.isfinite(s.p_adopted):
            last = s.t_computed
            print("  t+{:5.1f}s  P = {:.4f} s  (peak {:.4f} s, x{:g})  "
                  "power {:.3f}  FAP {:.1e}  {} pts / {:.1f} s".format(
                      args.headless - (t_end - time.perf_counter()),
                      s.p_adopted, s.p_peak, s.harmonic, s.power, s.fap,
                      s.n_samples, s.span))
    state.running = False


def _final_report(state: State, source: FrameSource, args):
    with state.lock:
        s = state.solution
        n_frames, n_sat = state.n_frames, state.n_sat
    t, _ = state.snapshot()

    print()
    print("=" * 62)
    print("Live session summary -- {} source".format(source.name))
    print("=" * 62)
    print("  frames processed : {}".format(n_frames))
    if t.size > 2:
        dt = np.diff(t)
        print("  measured cadence : {:.2f} fps  (jitter {:.2f} ms rms)".format(
            1.0 / np.median(dt), 1e3 * np.std(dt)))
        # A webcam's nominal rate is a request, not a promise. If the two differ
        # much, anything using frame index as a time base would be wrong -- which
        # is why this tool does not.
        if abs(1.0 / np.median(dt) - source.nominal_fps) > 0.05 * source.nominal_fps:
            print("    ! nominal was {:.1f} fps. Frame index would be a bad time"
                  " base here; real timestamps are being used.".format(
                      source.nominal_fps))
    if n_sat:
        print("  ! {} saturated frames -- glint peaks were clipped".format(n_sat))
    if np.isfinite(s.p_adopted):
        print("  recovered period : {:.4f} s   (LS peak {:.4f} s, harmonic x{:g})"
              .format(s.p_adopted, s.p_peak, s.harmonic))
        print("  peak power       : {:.3f}   FAP {:.2e}".format(s.power, s.fap))
        if np.isfinite(s.p_env):
            print("  envelope period  : {:.3f} s  (Axis B band, below {:.2f} Hz)"
                  .format(s.p_env, args.split))
        if source.name == "synthetic":
            truth = getattr(source, "truth", None)
            if truth is not None:
                # The live walk may stop one rung short of the full period when
                # the buffer is small, so the honest comparison is against
                # truth/n for the integer n it actually locked to -- quoting a
                # bare 50 % error for a clean P_A/2 lock says the tool failed
                # when it did not.
                n = max(int(round(truth.p_spin / s.p_adopted)), 1)
                err = 100 * abs(n * s.p_adopted - truth.p_spin) / truth.p_spin
                print("  commanded P_A    : {:.4f} s".format(truth.p_spin))
                if n == 1:
                    print("  error vs truth   : {:.3f} %".format(err))
                elif err < 2.0:
                    print("  locked to P_A/{}  : {:.4f} s x {} = {:.4f} s  "
                          "-> {:.3f} %".format(n, s.p_adopted, n,
                                               n * s.p_adopted, err))
                    print("    The walk stopped one rung short. It needs enough")
                    print("    cycles to see the target asymmetry above the noise;")
                    print("    a longer --window or a higher frame rate resolves it.")
                else:
                    print("  error vs truth   : {:.3f} % -- no harmonic match"
                          .format(100 * abs(s.p_adopted - truth.p_spin)
                                  / truth.p_spin))
    else:
        print("  no period recovered: {}".format(s.note or "insufficient data"))
    if args.record:
        print("  samples recorded -> {}".format(args.record))
        print("  Re-analyse offline with the full pipeline for a result of record;")
        print("  this display is a bench aid, not the measurement.")
    print("=" * 62)


if __name__ == "__main__":
    raise SystemExit(main())
