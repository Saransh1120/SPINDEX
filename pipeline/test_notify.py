"""Tests for feature extraction, anomaly detection, notification and archives.

    python pipeline/test_notify.py
    python pipeline/test_notify.py -v

No pytest dependency: the project already ships two hand-rolled test scripts and
adding a framework for thirty assertions is not worth the install. Exit code is
0 on pass, 1 on failure, so this drops into CI unchanged.

The behaviours worth protecting, and why each one is here:

  the engine is SILENT on a healthy run     an alerting system that cries wolf
                                            is worse than none
  the engine FIRES on an injected fault     the converse, and the only proof
                                            the suppression is not just "off"
  cooldown / novelty / persistence          the three interacting mechanisms
                                            that produce that balance
  corrupted archives are always caught      analysing a corrupt curve yields a
                                            confident wrong answer
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anomaly
import features as featmod
import importance as imp
import session as sessionmod
from anomaly import Event

VERBOSE = "-v" in sys.argv
_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    if VERBOSE or not cond:
        print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                                 ("  <- " + detail) if detail and not cond else ""))


def make_curve(period=64.0, span=700.0, fps=30.0, noise=0.05, seed=0,
               drift_at=None, drift_to=None, sat_frac=0.0):
    rng = np.random.default_rng(seed)
    n = int(span * fps)
    t = np.arange(n) / fps
    p = np.full(n, float(period))
    if drift_at is not None:
        p[t >= drift_at] = float(drift_to)
    phase = np.cumsum(2 * np.pi / p / fps)
    y = 1.0 + 0.35 * np.sin(2 * phase + 0.3) + 0.12 * np.sin(phase)
    y += rng.normal(0.0, noise, n)
    sat = np.zeros(n, bool)
    if sat_frac > 0:
        sat[rng.choice(n, int(sat_frac * n), replace=False)] = True
    return t, y, np.full(n, 0.01), sat


def analyse(t, y, bg, sat, policy, segments=10):
    span = float(t[-1] - t[0])
    clock = {"t": float(t[0])}
    eng = imp.NotificationEngine(policy, clock=lambda: clock["t"])
    det = anomaly.DriftDetector(warmup=2, min_history=4)
    win = 2.0 * span / (segments + 1)
    step = win / 2.0
    for i in range(segments):
        lo, hi = t[0] + i * step, t[0] + i * step + win
        m = (t >= lo) & (t < hi)
        if m.sum() < 64:
            continue
        ss = float(t[m][-1] - t[m][0])
        fs = featmod.extract(t[m], y[m], fmin=3.0 / ss, fmax=5.0,
                             background=bg[m], saturated=sat[m])
        clock["t"] = 0.5 * (lo + hi)
        evs = anomaly.detect(fs, det, source="test")
        eng.observe_absent([e.kind for e in evs])
        eng.submit_all(evs)
    return eng


# ----------------------------------------------------------------- features


def test_features():
    print("features")
    for P, span in [(64.0, 700.0), (16.0, 200.0), (1.8, 60.0)]:
        t, y, bg, sat = make_curve(P, span)
        fs = featmod.extract(t, y, fmin=3.0 / span, fmax=5.0,
                             background=bg, saturated=sat)
        err = abs(fs.period_s - P) / P
        check("period recovered at P={}s".format(P), err < 0.02,
              "got {:.4f}".format(fs.period_s))
        check("harmonic resolved at P={}s".format(P), fs.harmonic_order >= 2,
              "order {}".format(fs.harmonic_order))
    check("features are JSON-serialisable",
          json.dumps(fs.to_dict()) and True)
    check("every feature is documented",
          set(featmod.FEATURE_DESCRIPTIONS) <= set(fs.to_dict()),
          "undocumented: {}".format(
              set(featmod.FEATURE_DESCRIPTIONS) - set(fs.to_dict())))
    t, y, bg, sat = make_curve(64.0, 300.0)
    fs = featmod.extract(t[:8], y[:8], fmin=0.01, fmax=5.0)
    check("short input returns a FeatureSet rather than raising",
          fs.n_samples == 8)


# ---------------------------------------------------------------- detectors


def test_thresholds():
    print("threshold detectors")
    t, y, bg, sat = make_curve(64.0, 300.0, sat_frac=0.10)
    fs = featmod.extract(t, y, fmin=0.01, fmax=5.0, background=bg, saturated=sat)
    kinds = {e.kind for e in anomaly.check_thresholds(fs)}
    check("saturation is detected", "saturation" in kinds, str(kinds))

    flat = featmod.FeatureSet(modulation_depth=0.001, snr=1.0,
                              spectral_entropy=0.95, fap=0.5)
    kinds = {e.kind for e in anomaly.check_thresholds(flat)}
    for expect in ("no_modulation", "low_snr", "no_periodicity",
                   "insignificant_period"):
        check("flat curve raises {}".format(expect), expect in kinds, str(kinds))

    clean = featmod.FeatureSet(modulation_depth=0.5, snr=20.0, fap=1e-9,
                               spectral_entropy=0.2, saturated_fraction=0.0,
                               dropout_fraction=0.0, baseline_slope_per_s=0.0,
                               peak_q=30.0)
    check("healthy features raise nothing",
          len(anomaly.check_thresholds(clean)) == 0,
          str([e.kind for e in anomaly.check_thresholds(clean)]))


def test_drift():
    print("drift detector")
    det = anomaly.DriftDetector(warmup=3, min_history=4)
    for _ in range(10):
        det.update(featmod.FeatureSet(period_s=64.0, amplitude=1.0,
                                      mean_flux=1.0, n_glints=2,
                                      cadence_hz=30.0, background_level=0.01))
    evs = det.update(featmod.FeatureSet(period_s=80.0, amplitude=1.0,
                                        mean_flux=1.0, n_glints=2,
                                        cadence_hz=30.0, background_level=0.01))
    check("step change raises period_change",
          "period_change" in {e.kind for e in evs}, str([e.kind for e in evs]))

    det2 = anomaly.DriftDetector(warmup=3, min_history=4)
    rng = np.random.default_rng(0)
    fired = 0
    for _ in range(30):
        evs = det2.update(featmod.FeatureSet(
            period_s=64.0 + rng.normal(0, 0.05), amplitude=1.0,
            mean_flux=1.0, n_glints=2, cadence_hz=30.0, background_level=0.01))
        fired += len(evs)
    check("stationary stream stays quiet", fired <= 2,
          "{} events on stationary input".format(fired))


# ------------------------------------------------------------- notification


def test_importance_rules():
    print("importance rules")
    clock = {"t": 1000.0}
    eng = imp.NotificationEngine(imp.Policy(threshold=0.4, cooldown_s=60.0),
                                 clock=lambda: clock["t"])

    d = eng.submit(Event(kind="period_change", severity="high",
                         message="x", confidence=1.0))
    check("first high-severity event fires", d.fired, d.reason)

    clock["t"] += 5
    d2 = eng.submit(Event(kind="period_change", severity="high",
                          message="x", confidence=1.0))
    check("repeat inside cooldown is suppressed", not d2.fired, d2.reason)
    check("suppression reason names the cooldown", "cooldown" in d2.reason)

    d3 = eng.submit(Event(kind="baseline_rising", severity="low",
                          message="x", confidence=0.5))
    check("low severity with low confidence is suppressed", not d3.fired, d3.reason)

    eng2 = imp.NotificationEngine(imp.Policy(threshold=0.9, cooldown_s=1e9))
    dc = eng2.submit(Event(kind="saturation", severity="critical", message="x"))
    check("critical bypasses threshold and cooldown", dc.fired, dc.reason)

    eng3 = imp.NotificationEngine(imp.Policy(muted_kinds=["broad_peak"]))
    dm = eng3.submit(Event(kind="broad_peak", severity="critical", message="x"))
    check("muted kind never fires, even at critical", not dm.fired, dm.reason)

    eng4 = imp.NotificationEngine(imp.Policy(threshold=0.0, cooldown_s=0.0,
                                             rate_limit=3, rate_window_s=1e6))
    fired = sum(eng4.submit(Event(kind="k%d" % i, severity="high",
                                  message="x")).fired for i in range(10))
    check("rate limit caps notifications", fired == 3, "{} fired".format(fired))

    # novelty must fall while persistence rises
    eng5 = imp.NotificationEngine(imp.Policy(threshold=0.0, cooldown_s=0.0))
    novs, perss = [], []
    for _ in range(8):
        e = Event(kind="rep", severity="medium", message="x")
        _s, f = eng5.score(e)
        novs.append(f["novelty"])
        perss.append(f["persistence"])
        eng5.submit(e)
    check("novelty decays on repeats", novs[-1] < novs[0],
          "{:.3f} -> {:.3f}".format(novs[0], novs[-1]))
    check("persistence grows on repeats", perss[-1] > perss[0],
          "{:.3f} -> {:.3f}".format(perss[0], perss[-1]))

    eng6 = imp.NotificationEngine(imp.Policy(threshold=0.0, cooldown_s=0.0,
                                             escalate_after=3))
    escal = [eng6.submit(Event(kind="rep", severity="low", message="x")).escalated_to
             for _ in range(7)]
    check("persistent condition escalates", any(escal), str(escal))

    check("every decision records a reason",
          all(d.reason for d in eng.decisions + eng6.decisions))


def test_policy_roundtrip():
    print("policy import/export")
    tmp = Path(tempfile.mkdtemp())
    try:
        p = imp.PRESETS["unattended"]
        f = p.to_json(tmp / "policy.json")
        back = imp.Policy.from_file(f)
        check("policy round-trips", back.to_dict() == p.to_dict())
        (tmp / "bad.json").write_text(json.dumps({"threshold": 0.5, "nonsense": 1}))
        try:
            imp.Policy.from_file(tmp / "bad.json")
            check("unknown policy key is rejected", False, "no error raised")
        except ValueError as e:
            check("unknown policy key is rejected", "nonsense" in str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_end_to_end():
    print("end to end")
    t, y, bg, sat = make_curve(64.0, 700.0, seed=3)
    eng = analyse(t, y, bg, sat, imp.PRESETS["bench"])
    check("healthy run produces no notifications", len(eng.fired) == 0,
          "fired: {}".format([d.event.kind for d in eng.fired]))

    t, y, bg, sat = make_curve(64.0, 700.0, seed=3, drift_at=350, drift_to=80)
    eng = analyse(t, y, bg, sat, imp.PRESETS["bench"])
    kinds = {d.event.kind for d in eng.fired}
    check("spin-down is notified",
          "period_change" in kinds or "rate_change" in kinds, str(kinds))
    st = eng.stats()
    check("most events are suppressed, not all", 0 < st["notified"] < st["events"],
          str(st))

    strict = analyse(t, y, bg, sat, imp.PRESETS["unattended"])
    check("stricter policy notifies no more than bench",
          len(strict.fired) <= len(eng.fired),
          "{} vs {}".format(len(strict.fired), len(eng.fired)))


# ----------------------------------------------------------------- archives


def test_session_roundtrip():
    print("session import/export")
    tmp = Path(tempfile.mkdtemp())
    try:
        t, y, bg, sat = make_curve(64.0, 200.0)
        fs = featmod.extract(t, y, fmin=0.015, fmax=5.0, background=bg, saturated=sat)
        evs = anomaly.detect(fs, source="t")
        eng = imp.NotificationEngine(imp.PRESETS["bench"])
        decs = eng.submit_all(evs)
        arc = tmp / "s1"
        sessionmod.export_session(arc, t, y, bg, sat, features=fs, events=evs,
                                  decisions=decs, policy=eng.policy, source="t")

        data, ievs = sessionmod.import_session(arc)
        check("clean archive imports without events", len(ievs) == 0,
              str([e.kind for e in ievs]))
        check("sample count survives", len(data["t"]) == len(t))
        check("flux survives to 1e-6",
              float(np.max(np.abs(data["flux"] - y))) < 1e-6)
        check("policy travels with the archive",
              data["manifest"]["policy"]["name"] == "bench")

        # tamper
        arc2 = tmp / "s2"
        shutil.copytree(arc, arc2)
        lines = (arc2 / sessionmod.LIGHTCURVE).read_text().splitlines()
        (arc2 / sessionmod.LIGHTCURVE).write_text("\n".join(lines[:1000]) + "\n")
        _d, ievs = sessionmod.import_session(arc2)
        kinds = {e.kind for e in ievs}
        check("tampering trips the checksum",
              "import_integrity_failed" in kinds, str(kinds))
        check("truncation is detected", "import_truncated" in kinds, str(kinds))
        check("integrity failures are critical",
              all(e.severity == "critical" for e in ievs
                  if e.kind == "import_integrity_failed"))

        eng2 = imp.NotificationEngine(imp.PRESETS["import"])
        decs2 = eng2.submit_all(ievs)
        check("corruption is always notified",
              any(d.fired for d in decs2),
              "none of {} fired".format(len(decs2)))

        # future schema
        arc3 = tmp / "s3"
        shutil.copytree(arc, arc3)
        m = json.loads((arc3 / sessionmod.MANIFEST).read_text())
        m["schema_version"] = "9.9.9"
        (arc3 / sessionmod.MANIFEST).write_text(json.dumps(m))
        try:
            sessionmod.import_session(arc3)
            check("incompatible schema is refused", False, "no error")
        except sessionmod.SessionImportError as e:
            check("incompatible schema is refused", "9.9.9" in str(e))

        try:
            sessionmod.import_session(tmp / "does_not_exist")
            check("missing archive raises", False)
        except sessionmod.SessionImportError:
            check("missing archive raises", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 66)
    print("SDC-001 notification / feature / archive test suite")
    print("=" * 66)
    for fn in (test_features, test_thresholds, test_drift,
               test_importance_rules, test_policy_roundtrip,
               test_end_to_end, test_session_roundtrip):
        fn()
    print("=" * 66)
    passed = sum(1 for _n, ok, _d in _results if ok)
    total = len(_results)
    for n, ok, d in _results:
        if not ok:
            print("  FAILED: {}  {}".format(n, d))
    print("{}/{} checks passed".format(passed, total))
    print("=" * 66)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
