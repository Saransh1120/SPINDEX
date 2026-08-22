"""Session import/export with integrity checking and importance-gated notices.

This is the import/export subsystem the notification rules attach to. Before,
"export" meant scattering a CSV, a text report and a JSON blob into results/
with no version, no checksum and no way to tell whether a file had been
truncated, hand-edited, or produced by a different build. "Import" meant
np.loadtxt and hope.

A session archive is a single directory:

    session_<id>/
        manifest.json     schema version, provenance, policy, checksums
        lightcurve.csv    t, flux, background, saturated
        features.json     the extracted feature vector
        events.jsonl      every event, one JSON object per line
        decisions.jsonl   every notification decision, with its reasoning

Design decisions worth stating:

* **Checksums are over content, not metadata.** SHA-256 of each data file is
  recorded in the manifest. An import that reads a file whose digest does not
  match raises an integrity event at "critical" -- silently analysing a corrupt
  light curve produces a confident wrong period, which is the failure mode this
  whole project exists to avoid.

* **Import emits events rather than exceptions**, wherever it can still return
  usable data. A truncated file is worth flagging loudly but is often still
  analysable; refusing to load it helps nobody. Only an unreadable or
  schema-incompatible archive raises.

* **The policy travels with the archive.** A notification history you cannot
  reproduce is not evidence, so the manifest records which policy produced the
  decisions in decisions.jsonl.
"""

from __future__ import annotations

import csv
import hashlib
import json
import platform
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from anomaly import Event

SCHEMA_VERSION = "1.0.0"
SCHEMA_COMPATIBLE = {"1.0.0"}          # versions this build can read

MANIFEST = "manifest.json"
LIGHTCURVE = "lightcurve.csv"
FEATURES = "features.json"
EVENTS = "events.jsonl"
DECISIONS = "decisions.jsonl"

LIGHTCURVE_COLUMNS = ["t_s", "flux", "background", "saturated"]


def sha256_file(path, chunk=1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _provenance() -> dict:
    return {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": np.__version__,
    }


# --------------------------------------------------------------------- export


def export_session(outdir, t, flux, background=None, saturated=None,
                   features=None, events=None, decisions=None, policy=None,
                   source="", notes="", extra=None) -> Path:
    """Write a complete, self-describing, checksummed session archive."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    t = np.asarray(t, float)
    flux = np.asarray(flux, float)
    n = t.size
    background = np.zeros(n) if background is None else np.asarray(background, float)
    saturated = np.zeros(n, bool) if saturated is None else np.asarray(saturated, bool)
    if not (flux.size == background.size == saturated.size == n):
        raise ValueError(
            "column length mismatch: t={}, flux={}, background={}, saturated={}"
            .format(n, flux.size, background.size, saturated.size))

    lc = outdir / LIGHTCURVE
    with open(lc, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(LIGHTCURVE_COLUMNS)
        for i in range(n):
            w.writerow(["{:.6f}".format(t[i]), "{:.8g}".format(flux[i]),
                        "{:.8g}".format(background[i]), int(saturated[i])])

    (outdir / FEATURES).write_text(json.dumps(
        features.to_dict() if hasattr(features, "to_dict") else (features or {}),
        indent=2))

    with open(outdir / EVENTS, "w") as fh:
        for e in (events or []):
            fh.write(json.dumps(e.to_dict() if hasattr(e, "to_dict") else e) + "\n")

    with open(outdir / DECISIONS, "w") as fh:
        for d in (decisions or []):
            fh.write(json.dumps(d.to_dict() if hasattr(d, "to_dict") else d) + "\n")

    # Checksums are computed after every data file is closed, so the manifest
    # describes exactly what is on disk.
    checksums = {name: sha256_file(outdir / name)
                 for name in (LIGHTCURVE, FEATURES, EVENTS, DECISIONS)}

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "notes": notes,
        "provenance": _provenance(),
        "n_samples": int(n),
        "span_s": float(t[-1] - t[0]) if n > 1 else 0.0,
        "columns": LIGHTCURVE_COLUMNS,
        "checksums": checksums,
        "counts": {"events": len(events or []), "decisions": len(decisions or []),
                   "notified": sum(1 for d in (decisions or [])
                                   if (d.fired if hasattr(d, "fired")
                                       else d.get("fired")))},
        "policy": (policy.to_dict() if hasattr(policy, "to_dict") else policy),
        "extra": extra or {},
    }
    (outdir / MANIFEST).write_text(json.dumps(manifest, indent=2))
    return outdir


# --------------------------------------------------------------------- import


class SessionImportError(Exception):
    """Raised only when the archive cannot yield usable data at all."""


def import_session(indir, verify_checksums=True, expect_span_s=None):
    """Load an archive, returning (data, events).

    ``events`` describes problems found while importing -- schema drift, digest
    mismatches, truncation, unusable columns. They are ordinary Events so the
    same importance engine that gates live alerts also gates import notices,
    which is the point: one bar, one audit trail, whatever the source.
    """
    indir = Path(indir)
    events: list[Event] = []
    src = indir.name

    if not indir.is_dir():
        raise SessionImportError("not a directory: {}".format(indir))
    mpath = indir / MANIFEST
    if not mpath.exists():
        raise SessionImportError(
            "no {} in {} - not a session archive".format(MANIFEST, indir))

    try:
        manifest = json.loads(mpath.read_text())
    except json.JSONDecodeError as e:
        raise SessionImportError("manifest is not valid JSON: {}".format(e))

    ver = manifest.get("schema_version")
    if ver not in SCHEMA_COMPATIBLE:
        events.append(Event(
            kind="import_schema_invalid", severity="critical", source=src,
            message="archive schema {} is not readable by this build (accepts {})"
                    .format(ver, sorted(SCHEMA_COMPATIBLE)),
            detail={"found": ver, "accepts": sorted(SCHEMA_COMPATIBLE)}))
        raise SessionImportError(
            "incompatible schema {}; refusing to guess at the layout".format(ver))

    if verify_checksums:
        for name, expect in (manifest.get("checksums") or {}).items():
            f = indir / name
            if not f.exists():
                events.append(Event(
                    kind="import_integrity_failed", severity="critical",
                    source=src, message="{} is missing from the archive".format(name),
                    detail={"file": name}))
                continue
            got = sha256_file(f)
            if got != expect:
                events.append(Event(
                    kind="import_integrity_failed", severity="critical",
                    source=src,
                    message="{} fails its checksum - the file has changed since "
                            "export".format(name),
                    detail={"file": name, "expected": expect[:16],
                            "found": got[:16]}))
    return _load_payload(indir, manifest, events, src, expect_span_s)


def _load_payload(indir, manifest, events, src, expect_span_s):
    lc = indir / LIGHTCURVE
    if not lc.exists():
        raise SessionImportError("no {} in archive".format(LIGHTCURVE))

    rows, bad_rows = [], 0
    with open(lc, newline="") as fh:
        r = csv.reader(fh)
        header = next(r, None)
        if header != LIGHTCURVE_COLUMNS:
            events.append(Event(
                kind="import_schema_invalid", severity="high", source=src,
                message="light-curve columns are {} not {}".format(
                    header, LIGHTCURVE_COLUMNS),
                detail={"found": header, "expected": LIGHTCURVE_COLUMNS}))
        for row in r:
            try:
                rows.append((float(row[0]), float(row[1]), float(row[2]),
                             bool(int(row[3]))))
            except (ValueError, IndexError):
                bad_rows += 1

    if not rows:
        raise SessionImportError("light curve contains no usable rows")

    arr = np.array([(a, b, c) for a, b, c, _ in rows], float)
    sat = np.array([d for *_x, d in rows], bool)
    data = {"t": arr[:, 0], "flux": arr[:, 1], "background": arr[:, 2],
            "saturated": sat, "manifest": manifest}

    if bad_rows:
        events.append(Event(
            kind="import_truncated", severity="high", source=src,
            confidence=1.0,
            message="{} unparseable row(s) skipped - the file may be truncated "
                    "or hand-edited".format(bad_rows),
            detail={"bad_rows": bad_rows, "good_rows": len(rows)}))

    # Row count against the manifest. A short file that still parses cleanly is
    # the dangerous case: nothing looks wrong, but the record is incomplete.
    declared = manifest.get("n_samples")
    if isinstance(declared, int) and declared != len(rows):
        frac = 1.0 - len(rows) / max(declared, 1)
        events.append(Event(
            kind="import_truncated",
            severity="critical" if frac > 0.10 else "high",
            source=src, value=float(len(rows)),
            expected_low=float(declared), expected_high=float(declared),
            confidence=1.0,
            message="archive declares {} samples but holds {} ({:.1%} missing)"
                    .format(declared, len(rows), frac),
            detail={"declared": declared, "found": len(rows)}))

    span = float(arr[-1, 0] - arr[0, 0]) if len(rows) > 1 else 0.0
    if expect_span_s and span < 0.8 * expect_span_s:
        events.append(Event(
            kind="import_truncated", severity="high", source=src, value=span,
            expected_low=expect_span_s, confidence=0.9,
            message="record spans {:.1f} s, short of the {:.1f} s expected"
                    .format(span, expect_span_s)))

    # Non-monotonic timestamps break every downstream assumption about ordering.
    dt = np.diff(arr[:, 0])
    if dt.size and np.any(dt <= 0):
        events.append(Event(
            kind="import_schema_invalid", severity="critical", source=src,
            message="timestamps are not strictly increasing at {} point(s)"
                    .format(int(np.sum(dt <= 0))),
            detail={"n_bad": int(np.sum(dt <= 0))}))

    fpath = indir / FEATURES
    if fpath.exists():
        try:
            data["features"] = json.loads(fpath.read_text())
        except json.JSONDecodeError:
            events.append(Event(
                kind="import_schema_invalid", severity="medium", source=src,
                message="features.json is not valid JSON; continuing without it"))

    for name, key in ((EVENTS, "events"), (DECISIONS, "decisions")):
        p = indir / name
        recs = []
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        data[key] = recs

    return data, events


def summarise(indir) -> str:
    """One-screen description of an archive, without loading the light curve."""
    m = json.loads((Path(indir) / MANIFEST).read_text())
    c = m.get("counts", {})
    return "\n".join([
        "archive   {}".format(Path(indir).name),
        "schema    {}".format(m.get("schema_version")),
        "source    {}".format(m.get("source") or "-"),
        "created   {}".format((m.get("provenance") or {}).get("created_utc")),
        "samples   {}  span {:.1f} s".format(m.get("n_samples"), m.get("span_s", 0.0)),
        "events    {}   notified {}".format(c.get("events", 0), c.get("notified", 0)),
        "policy    {}".format((m.get("policy") or {}).get("name", "-")),
    ])
