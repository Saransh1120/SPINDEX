"""Anomaly detection over the feature stream.

Consumes FeatureSet records and emits Events. It answers "what changed?", not
"is this important?" -- importance is a separate decision made in importance.py,
because the two have genuinely different lifetimes: what counts as anomalous is
a property of the physics, while what counts as worth interrupting someone for
is a property of the operator's situation.

Two detector families:

  threshold   Absolute, physically-motivated limits. Saturation above a few
              percent is bad regardless of history. These fire on the first
              observation, which matters because the most damaging faults --
              a clipped glint, auto-exposure left on -- are present from frame
              one and never "change".

  drift       Robust change detection against a rolling baseline of the same
              feature. Uses median and MAD rather than mean and standard
              deviation: a single spike would inflate an SD-based band enough to
              hide the very excursion it should catch.

Every Event carries the evidence that produced it -- feature name, observed
value, expected range -- so a notification can be justified after the fact
rather than merely asserted.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from typing import Iterable

import numpy as np

SCHEMA_VERSION = "1.0.0"

# Severity ladder. Ordering matters -- importance.py compares these.
SEVERITY_ORDER = ("info", "low", "medium", "high", "critical")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}


@dataclass
class Event:
    """One detected condition, with the evidence behind it."""

    kind: str                      # stable machine-readable identifier
    severity: str                  # one of SEVERITY_ORDER
    message: str                   # one line, human-readable
    feature: str = ""              # which feature triggered it
    value: float = float("nan")
    expected_low: float = float("nan")
    expected_high: float = float("nan")
    confidence: float = 1.0        # 0-1; how sure the detector is
    t: float = field(default_factory=time.time)
    source: str = ""               # dataset / session identifier
    detail: dict = field(default_factory=dict)

    @property
    def rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = SCHEMA_VERSION
        return d

    def one_line(self) -> str:
        return "[{:^8}] {:<22} {}".format(self.severity.upper(), self.kind, self.message)


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------- threshold rules
#
# (feature, comparison, limit, severity, kind, message template)
# Limits are physically motivated, and each one is defended in the comment
# beside it rather than being a round number someone liked.

THRESHOLD_RULES = [
    # Saturation destroys the glint peaks, which is where the geometric
    # information lives. Rev B section 8.4. Any is bad; 2 % is intolerable.
    ("saturated_fraction", ">", 0.02, "high", "saturation",
     "clipped pixels in {value:.1%} of frames - shorten the exposure"),
    ("saturated_fraction", ">", 0.0001, "low", "saturation_trace",
     "occasional clipping ({value:.2%} of frames)"),

    # Stray light above 2 % of peak collapses the modulation depth that carries
    # the geometry. Rev B section 8.3.
    ("modulation_depth", "<", 0.02, "high", "no_modulation",
     "modulation depth {value:.3f} - target may be lost, unlit, or swamped"),

    # A false-alarm probability this high means the period is not evidence.
    ("fap", ">", 0.01, "medium", "insignificant_period",
     "false-alarm probability {value:.2g} - period is not significant"),

    # Featureless spectrum: no preferred frequency at all.
    ("spectral_entropy", ">", 0.85, "medium", "no_periodicity",
     "spectral entropy {value:.2f} - spectrum is close to featureless noise"),

    # Below this the fold is dominated by scatter and the shape features are
    # not meaningful even if the period is.
    ("snr", "<", 3.0, "medium", "low_snr",
     "SNR {value:.1f} - light curve is close to the noise floor"),

    # Dropped frames break the assumption the time base is regular. The live
    # path timestamps every sample so it survives this, but a large dropout
    # fraction means the capture chain is struggling.
    ("dropout_fraction", ">", 0.05, "medium", "frame_dropouts",
     "{value:.1%} of intervals exceed 3x the median - capture is dropping frames"),

    # An unheatsunk lamp drooping, or auto-exposure hunting. Either way the
    # baseline is moving under the measurement.
    ("baseline_slope_per_s", ">", 0.002, "low", "baseline_rising",
     "baseline rising {value:.3%} per second"),
    ("baseline_slope_per_s", "<", -0.002, "low", "baseline_falling",
     "baseline falling {value:.3%} per second - lamp droop or AE hunting"),

    # A broad peak means the rate is not coherent, even though a period is
    # reported. Q below 5 is a smear, not a line.
    ("peak_q", "<", 5.0, "low", "broad_peak",
     "periodogram peak Q {value:.1f} - rotation rate is not coherent"),
]

_CMP = {">": lambda a, b: a > b, "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b}


def check_thresholds(fs, source: str = "") -> list[Event]:
    d = fs.to_dict() if hasattr(fs, "to_dict") else dict(fs)
    events = []
    for feat, op, limit, sev, kind, template in THRESHOLD_RULES:
        v = d.get(feat)
        if not _finite(v):
            continue
        if _CMP[op](float(v), limit):
            events.append(Event(
                kind=kind, severity=sev, feature=feat, value=float(v),
                expected_low=limit if op in ("<", "<=") else float("nan"),
                expected_high=limit if op in (">", ">=") else float("nan"),
                message=template.format(value=float(v)), source=source,
                confidence=1.0))
    # A trace-level saturation event is redundant once the high one fires.
    kinds = {e.kind for e in events}
    if "saturation" in kinds:
        events = [e for e in events if e.kind != "saturation_trace"]
    return events


# ------------------------------------------------------------------- drift rules
#
# (feature, n_mad, min_history, severity, kind, message template)
# n_mad is how many robust deviations from the rolling median counts as a
# change. 4 is roughly a 2.7-sigma equivalent for Gaussian noise once the MAD
# scaling is applied, chosen so a well-behaved run produces no events at all.

# (feature, n_mad, min_history, min_frac_change, severity, kind, template)
#
# Two conditions must BOTH hold before a drift event is raised: the value must
# be n_mad robust deviations from the rolling median, AND it must have moved by
# at least min_frac_change in relative terms.
#
# The second condition is not redundant. A feature that has been almost perfectly
# constant has an almost zero MAD, so the z-score explodes on the first bit of
# ordinary noise and everything looks significant -- measured: a healthy
# synthetic run raised amplitude_change and morphology_change purely from
# realisation noise. The fractional floor asks "is this change big enough to
# care about?", which the z-score alone cannot answer.

DRIFT_RULES = [
    ("period_s", 4.0, 6, 0.02, "high", "period_change",
     "period moved to {value:.4f} s from a baseline of {median:.4f} s"),
    # The raw periodogram peak, before the sub-harmonic walk. Tracked as well as
    # the adopted period because the walk is a discrete decision: on short
    # windows it can resolve x2 in one window and x1 in the next, which reads as
    # a 100 % period jump that is an artefact of the analysis, not the target.
    ("ls_peak_period_s", 4.0, 6, 0.02, "high", "rate_change",
     "rotation rate moved: peak period {value:.4f} s from {median:.4f} s"),
    ("amplitude", 6.0, 8, 0.30, "medium", "amplitude_change",
     "fold amplitude changed to {value:.4f} from {median:.4f}"),
    ("mean_flux", 5.0, 8, 0.20, "low", "flux_level_change",
     "mean flux changed to {value:.4g} from {median:.4g}"),
    # Morphology change is tracked on duty_cycle, a continuous bounded feature,
    # NOT on n_glints. A glint count is a small integer: it quantises, so a
    # noise-induced flip from 1 to 2 is a 100 % change that no fractional floor
    # can distinguish from a real one. n_glints remains in the feature vector as
    # a descriptor -- it is genuinely informative about a target -- but it is
    # unfit as a change detector, and using it as one produced false alarms on
    # healthy runs.
    ("duty_cycle", 5.0, 8, 0.25, "medium", "morphology_change",
     "profile duty cycle changed to {value:.3f} from {median:.3f}"),
    ("glint_sharpness", 5.0, 8, 0.60, "low", "profile_shape_change",
     "profile kurtosis changed to {value:.2f} from {median:.2f}"),
    ("cadence_hz", 4.0, 8, 0.10, "medium", "cadence_change",
     "capture rate changed to {value:.2f} Hz from {median:.2f} Hz"),
    ("background_level", 5.0, 8, 0.50, "low", "background_change",
     "background changed to {value:.4g} from {median:.4g}"),
]

MAD_TO_SIGMA = 1.4826


class DriftDetector:
    """Rolling robust change detection over a stream of FeatureSets.

    Median and MAD, not mean and SD. With a mean/SD band a single large
    excursion inflates the band enough to mask itself and the next few samples;
    with median/MAD the baseline barely moves, so a step change stays visible
    for as long as it persists. That property is what makes this usable as a
    live monitor rather than a one-shot check.
    """

    def __init__(self, history=48, warmup=8, min_history=None):
        self.history = history
        self.warmup = warmup
        # Batch/segmented analysis has far fewer observations than a live run,
        # so the per-rule minimums can be lowered explicitly rather than the
        # detector silently never arming.
        self.min_history = min_history
        self._buf = defaultdict(lambda: deque(maxlen=history))

    def update(self, fs, source: str = "") -> list[Event]:
        d = fs.to_dict() if hasattr(fs, "to_dict") else dict(fs)
        events = []

        for feat, n_mad, min_hist, min_frac, sev, kind, template in DRIFT_RULES:
            v = d.get(feat)
            if not _finite(v):
                continue
            v = float(v)
            buf = self._buf[feat]

            need = self.min_history if self.min_history is not None else max(
                min_hist, self.warmup)
            if len(buf) >= need:
                arr = np.fromiter(buf, float)
                med = float(np.median(arr))
                mad = float(np.median(np.abs(arr - med))) * MAD_TO_SIGMA
                # Floor the scale so a perfectly constant history does not make
                # every subsequent sample infinitely anomalous.
                scale = max(mad, 1e-9 + 1e-3 * abs(med))
                z = abs(v - med) / scale
                frac = abs(v - med) / max(abs(med), 1e-12)
                if z > n_mad and frac >= min_frac:
                    events.append(Event(
                        kind=kind, severity=sev, feature=feat, value=v,
                        expected_low=med - n_mad * scale,
                        expected_high=med + n_mad * scale,
                        # Confidence saturates at 2x the trigger threshold, so a
                        # marginal excursion is not reported as a certainty.
                        confidence=float(min(1.0, z / (2.0 * n_mad))),
                        message=template.format(value=v, median=med),
                        source=source,
                        detail={"z_robust": round(z, 2), "n_mad": n_mad,
                                "frac_change": round(frac, 4),
                                "min_frac_change": min_frac,
                                "baseline_median": med, "baseline_mad": mad,
                                "history": len(buf)}))
            buf.append(v)
        return events

    def baseline(self, feat):
        buf = self._buf.get(feat)
        if not buf:
            return None
        arr = np.fromiter(buf, float)
        return {"median": float(np.median(arr)), "n": len(arr)}


def detect(fs, detector: DriftDetector | None = None, source: str = "") -> list[Event]:
    """Threshold checks plus, if a detector is supplied, drift checks."""
    events = check_thresholds(fs, source=source)
    if detector is not None:
        events += detector.update(fs, source=source)
    events.sort(key=lambda e: (-e.rank, e.kind))
    return events
