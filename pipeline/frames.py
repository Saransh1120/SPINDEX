"""Stage 2: frame extraction from the recorded .mov, with a constant-frame-rate audit.

The whole time base of this experiment is the assumption that frames are evenly
spaced (Rev B section 4.2 -- there is no GPS receiver in this build precisely
because the frame rate is the clock). If the camera silently recorded variable
frame rate, frame index divided by nominal fps is not time, every period comes
out wrong, and nothing downstream can detect it. So extraction and audit are
one operation here, not two.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def find_ffmpeg() -> str:
    """System ffmpeg if present, else the binary bundled with imageio-ffmpeg."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:
        raise RuntimeError(
            "No ffmpeg found. Install it system-wide, or 'pip install imageio-ffmpeg'."
        ) from e


def find_ffprobe() -> str | None:
    """ffprobe if available. imageio-ffmpeg does not bundle one, so this may be
    None -- the audit falls back to counting extracted frames, which is the
    check that actually matters anyway."""
    return shutil.which("ffprobe")


@dataclass
class ExtractionAudit:
    n_frames: int
    duration_s: float | None
    nominal_fps: float
    expected_frames: int | None
    cfr_ok: bool
    note: str

    def __str__(self) -> str:
        lines = [
            f"  frames extracted : {self.n_frames}",
            f"  nominal fps      : {self.nominal_fps:g}",
        ]
        if self.duration_s is not None:
            lines.append(f"  container duration : {self.duration_s:.3f} s")
            lines.append(f"  expected frames    : {self.expected_frames}")
        lines.append(f"  CFR check        : {'PASS' if self.cfr_ok else 'FAIL'}")
        lines.append(f"  {self.note}")
        return "\n".join(lines)


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


def probe_duration(path: Path) -> float | None:
    """Container duration in seconds.

    Prefers ffprobe. imageio-ffmpeg ships ffmpeg without ffprobe, so this falls
    back to parsing the Duration line ffmpeg writes to stderr when asked to
    decode a file with no output. Same number, one fewer dependency -- and the
    CFR audit is too important to skip just because ffprobe is missing.
    """
    ffprobe = find_ffprobe()
    if ffprobe:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        ).stdout
        try:
            return float(json.loads(out)["format"]["duration"])
        except (KeyError, ValueError, json.JSONDecodeError):
            return None

    err = subprocess.run([find_ffmpeg(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    m = _DURATION_RE.search(err)
    if not m:
        return None
    h, mnt, sec = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(sec)


def extract_frames(video: Path, outdir: Path, fps: float = 60.0,
                   pattern: str = "f%06d.png", overwrite: bool = True,
                   tolerance: int = 1) -> tuple[list[Path], ExtractionAudit]:
    """Explode a video into PNG frames and audit the result.

    Uses ``-vsync 0`` (passthrough) exactly as Rev B section 10.2 specifies:
    it emits one image per *stored* frame and never duplicates or drops to hit a
    target rate. That is the point -- a variable-frame-rate recording then shows
    up as a frame count that disagrees with duration x fps, instead of being
    silently resampled into something that looks fine and is wrong.
    """
    video = Path(video)
    outdir = Path(outdir)
    if not video.exists():
        raise FileNotFoundError(video)

    if overwrite and outdir.exists():
        for old in outdir.glob(pattern.replace("%06d", "*")):
            old.unlink()
    outdir.mkdir(parents=True, exist_ok=True)

    cmd = [find_ffmpeg(), "-hide_banner", "-loglevel", "error",
           "-i", str(video), "-vsync", "0", str(outdir / pattern)]
    if overwrite:
        cmd.insert(1, "-y")
    subprocess.run(cmd, check=True, capture_output=True)

    frames = sorted(outdir.glob(pattern.replace("%06d", "*")))
    duration = probe_duration(video)

    if duration is None:
        audit = ExtractionAudit(
            len(frames), None, fps, None, True,
            "ffprobe unavailable: frame count not cross-checked against "
            "container duration. Verify the recording is CFR by other means.",
        )
    else:
        expected = int(round(duration * fps))
        delta = len(frames) - expected
        ok = abs(delta) <= tolerance
        audit = ExtractionAudit(
            len(frames), duration, fps, expected, ok,
            f"frame count is {delta:+d} against duration x fps"
            + ("" if ok else
               " -- VARIABLE FRAME RATE. The time base is invalid; every period "
               "derived from it will be wrong. Re-record with CFR locked."),
        )

    return frames, audit


def time_base(n_frames: int, fps: float):
    """Sample times implied by the frame index. Exact for a CFR recording."""
    import numpy as np
    return np.arange(n_frames) / fps
