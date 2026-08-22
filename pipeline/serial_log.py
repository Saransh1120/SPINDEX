"""Read the Arduino ground-truth channel and reduce it to a rotation period.

    python pipeline/serial_log.py --port COM5 --duration 120
    python pipeline/serial_log.py --port COM5 --out data/serial/run01.csv
    python pipeline/serial_log.py --replay data/serial/synthetic_pulses_model_A.txt

The firmware prints "P <micros>" on each rising edge of the slot sensor, one per
revolution of Axis A. This is the independent truth channel of Rev B section
6.3: no camera, no codec, no photometry. When the inversion reports a period, it
is scored against this number.

Start this before the video recording and stop it after, so the pulse train
brackets the whole clip.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import numpy as np

PULSE_RE = re.compile(r"^P\s+(\d+)\s*$")
MICROS_WRAP = 2 ** 32          # micros() rolls over about every 71.6 minutes


def parse_line(line: str):
    """Return the micros() value from a pulse line, or None for anything else.

    Banner and OK/ERR lines share the stream, so non-matching lines are skipped
    rather than treated as errors.
    """
    m = PULSE_RE.match(line.strip())
    return int(m.group(1)) if m else None


def unwrap(pulses_us) -> np.ndarray:
    """Undo the micros() rollover, returning a monotonic microsecond series.

    A 120 s recording will not usually span a wrap, but it will if the board has
    been powered for an hour before the run -- which is exactly the situation
    during a long alignment session. Handling it costs one line; not handling it
    produces a single absurd interval that drags the mean period badly.
    """
    p = np.asarray(pulses_us, dtype=np.int64)
    if p.size < 2:
        return p.astype(float)
    d = np.diff(p)
    d = np.where(d < 0, d + MICROS_WRAP, d)
    return np.concatenate([[0.0], np.cumsum(d)]).astype(float)


def reduce_pulses(pulses_us, reject_frac: float = 0.25):
    """Mean rotation period and its scatter, with dropped-edge screening.

    Two estimates are returned because they fail differently:

      mean of intervals   Simple, but one missed edge produces a double-length
                          interval that biases it upward.
      slope of a fit      Least-squares slope of timestamp against edge number.
                          Uses the whole baseline, so its precision improves with
                          the number of edges rather than staying flat -- but it
                          counts edges, so a *dropped* edge makes it read one
                          revolution as two and biases it high. Prefer the mean
                          whenever n_rejected > 0.

    Intervals more than ``reject_frac`` from the median are reported, not
    silently dropped: a missed edge means the tab or the sensor needs attention
    before the run is trusted.
    """
    t_us = unwrap(pulses_us)
    if t_us.size < 3:
        raise ValueError("need at least 3 pulses to estimate a period")

    d = np.diff(t_us)
    med = float(np.median(d))
    bad = np.abs(d - med) > reject_frac * med
    good = d[~bad]

    mean_p = float(good.mean()) / 1e6
    std_p = float(good.std(ddof=1)) / 1e6 if good.size > 1 else float("nan")

    k = np.arange(t_us.size, dtype=float)
    slope, intercept = np.polyfit(k, t_us, 1)
    resid = t_us - (slope * k + intercept)
    # Standard error on the slope of a straight-line fit.
    sxx = float(((k - k.mean()) ** 2).sum())
    slope_err = float(resid.std(ddof=2) / np.sqrt(sxx)) if sxx > 0 else float("nan")

    return {
        "n_pulses": int(t_us.size),
        "n_rejected": int(bad.sum()),
        "median_interval_s": med / 1e6,
        "mean_period_s": mean_p,
        "std_period_s": std_p,
        "sem_period_s": std_p / np.sqrt(good.size) if good.size > 1 else float("nan"),
        "fit_period_s": float(slope) / 1e6,
        "fit_period_err_s": slope_err / 1e6,
        "span_s": float(t_us[-1] - t_us[0]) / 1e6,
    }


def read_serial(port: str, duration: float, baud: int = 115200,
                echo: bool = True, send: str | None = None):
    """Collect pulse timestamps from the controller for ``duration`` seconds."""
    try:
        import serial
    except ImportError:
        raise SystemExit("pyserial is required for live capture: "
                         "pip install pyserial\n"
                         "(use --replay to reduce an existing log instead)")

    pulses = []
    raw = []
    with serial.Serial(port, baud, timeout=0.2) as ser:
        # The Uno resets when the port opens; the bootloader takes ~1.5 s and
        # anything sent before it finishes is swallowed.
        time.sleep(2.0)
        ser.reset_input_buffer()
        if send:
            for cmd in send.split(";"):
                ser.write((cmd.strip() + "\n").encode())
                time.sleep(0.05)

        t_end = time.time() + duration
        while time.time() < t_end:
            line = ser.readline().decode("ascii", errors="replace")
            if not line:
                continue
            raw.append(line.rstrip())
            v = parse_line(line)
            if v is not None:
                pulses.append(v)
                if echo and len(pulses) % 10 == 0:
                    print("    {} pulses, {:.0f} s remaining"
                          .format(len(pulses), t_end - time.time()), end="\r")
            elif echo and line.startswith("#"):
                print("  " + line.rstrip())
    if echo:
        print(" " * 50, end="\r")
    return pulses, raw


def read_replay(path: Path):
    """Reduce a previously saved log or the synthetic pulse train."""
    pulses = []
    for line in Path(path).read_text().splitlines():
        v = parse_line(line)
        if v is None and "," in line:            # CSV written by this tool
            parts = line.split(",")
            if parts[-1].strip().isdigit():
                v = int(parts[-1])
        if v is not None:
            pulses.append(v)
    return pulses


def write_csv(path: Path, pulses, stats):
    path.parent.mkdir(parents=True, exist_ok=True)
    t_us = unwrap(pulses)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["edge", "micros_raw", "t_unwrapped_s", "interval_s"])
        for i, (raw, tu) in enumerate(zip(pulses, t_us)):
            iv = "" if i == 0 else "{:.6f}".format((t_us[i] - t_us[i - 1]) / 1e6)
            w.writerow([i, raw, "{:.6f}".format(tu / 1e6), iv])
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="e.g. COM5 on Windows, /dev/ttyACM0 on Linux")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--duration", type=float, default=120.0)
    ap.add_argument("--replay", help="reduce an existing log instead of capturing")
    ap.add_argument("--send", help="commands to send first, ';'-separated, "
                                   "e.g. 'A1730;B561'")
    ap.add_argument("--out", help="CSV output path")
    ap.add_argument("--expect", type=float, help="commanded period, for comparison")
    args = ap.parse_args(argv)

    if not args.port and not args.replay:
        ap.error("give --port for live capture or --replay for an existing log")

    if args.replay:
        print("Replaying {}".format(args.replay))
        pulses = read_replay(Path(args.replay))
        default_out = Path(args.replay).with_suffix(".csv")
    else:
        print("Capturing from {} at {} baud for {:.0f} s".format(
            args.port, args.baud, args.duration))
        pulses, _ = read_serial(args.port, args.duration, args.baud,
                                send=args.send)
        default_out = Path("data/serial/pulses_{}.csv".format(
            time.strftime("%Y%m%d_%H%M%S")))

    if len(pulses) < 3:
        raise SystemExit("only {} pulses captured. Check the tab passes through "
                         "the slot, the sensor LED lights, and D9 is wired."
                         .format(len(pulses)))

    s = reduce_pulses(pulses)
    out = write_csv(Path(args.out or default_out), pulses, s)

    print()
    print("=" * 60)
    print("Slot-sensor ground truth")
    print("=" * 60)
    print("  pulses captured   : {}".format(s["n_pulses"]))
    print("  span              : {:.3f} s".format(s["span_s"]))
    print("  intervals rejected: {}{}".format(
        s["n_rejected"],
        "  <- missed or spurious edges; check the tab and sensor alignment"
        if s["n_rejected"] else ""))
    print("  mean period       : {:.6f} +/- {:.6f} s  (sd {:.6f})".format(
        s["mean_period_s"], s["sem_period_s"], s["std_period_s"]))
    print("  fitted period     : {:.6f} +/- {:.6f} s  (slope over {} edges)".format(
        s["fit_period_s"], s["fit_period_err_s"], s["n_pulses"]))
    if s["n_rejected"]:
        print()
        print("  ! Trust the MEAN, not the fit, for this run. The slope fit counts")
        print("    edges, so a dropped edge makes it think one revolution took two")
        print("    and biases it high. The mean screens the bad interval out. With")
        print("    no rejections the fit is the better number -- it uses the whole")
        print("    baseline and its error shrinks with edge count.")
        print()
    if args.expect:
        for lab, key in (("mean", "mean_period_s"), ("fit", "fit_period_s")):
            print("  {} vs commanded {:.6f} s : {:+.4f} %".format(
                lab, args.expect, 100 * (s[key] - args.expect) / args.expect))
    print("  CSV -> {}".format(out))
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
