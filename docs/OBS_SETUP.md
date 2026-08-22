# Connecting an iPhone to the live monitor through OBS

Short answer: **yes, this works**, and it is a better sensor than the built-in
webcam on this machine. But OBS sits between the phone and the analysis, and it
changes two things that matter — the time base and the exposure control. Read
"What OBS changes" before trusting a number.

---

## 1. Requirements

| Piece | What | Notes |
|---|---|---|
| Phone | iPhone, any model with manual camera control | 60 fps preferred |
| Bridge app | **Camo**, **iVCam**, **EpocCam**, or **DroidCam** | Windows has no native iPhone capture; you need one of these |
| OBS Studio | 28 or later | Free, obsproject.com |
| Python deps | `pip install -r requirements.txt` | `opencv-python` is the one that matters here |

Already installed and verified on this machine: `opencv-python 5.0.0`,
`numpy 2.4.1`, `scipy 1.17.0`, `matplotlib 3.10.8`, `emcee 3.1.6`,
`pillow 12.1.0`, `pyserial 3.5`, `corner`, `imageio-ffmpeg`.

**USB, not Wi-Fi.** Every bridge app offers wireless. Do not use it. Wi-Fi
introduces variable latency of tens to hundreds of milliseconds, and that jitter
lands directly in your time base.

---

## 2. Steps

### On the phone
1. Install the bridge app's iOS side and connect by **USB cable**.
2. In the bridge app, set the camera to **manual/locked** for:
   - exposure (shutter + ISO)
   - white balance
   - focus
3. Set 60 fps if offered, otherwise 30.

Locking exposure **on the phone** is the important step. Do not rely on OBS or
on this software to do it — see §4.

### In OBS
4. Add a **Video Capture Device** source, pick the bridge app's camera.
5. **Settings → Video**: set Base and Output resolution the same (e.g.
   1280×720), and **Common FPS Values → 60** (or 30). Matching base and output
   avoids a rescale that softens the target.
6. **Settings → Advanced → Color Format: NV12, Color Space: sRGB, Color Range:
   Full.** Limited range crushes 0–15 and 236–255, which throws away glint peaks
   and shadow detail for no benefit.
7. Remove every filter from the source. No sharpening, no colour correction, no
   noise suppression, no LUT. Each one is a non-linear transform applied to the
   thing you are measuring.
8. Crop the scene so the target fills roughly a third of the short side. Too
   large and the background annulus falls off the frame; the software will shrink
   the aperture and warn, but framing it properly is better.
9. Click **Start Virtual Camera**.

### In this software
10. Find the device index:

```bash
python pipeline/live.py --list-devices
```

The OBS Virtual Camera reports your OBS canvas size. On this machine, index 0 is
the built-in webcam at 640×480 and index 1 already shows 1280×720 — that is
almost certainly it. If two entries look alike, stop the virtual camera, re-run,
and see which index disappears.

11. Run it:

```bash
python pipeline/live.py --source 1 --width 1280 --height 720 --fps 60 --window 60
```

12. Once you can see it working, record for offline analysis:

```bash
python pipeline/live.py --source 1 --fps 60 --window 60 --record data/serial/run01.csv
```

---

## 3. What you will see

Seven panels, updating live:

| Panel | Question it answers |
|---|---|
| Camera + aperture | Is the target inside the solid circle at every attitude, and is the dotted annulus clear of it? |
| Scrolling light curve | Are there glints, and is the modulation depth sane? |
| **P = x.xxxx s** | The recovered period, with rpm. Grey means not significant. |
| Diagnostics | LS peak, harmonic multiplier, envelope period, FAP, buffer fill, cadence, jitter, background, saturation count |
| Periodogram | One dominant peak or a forest? Red lines mark harmonics of the adopted period, orange marks the envelope |
| Phase fold | The strongest evidence. A correct period folds into one clean repeating profile; a wrong one smears |
| Period history | Is the answer stable, or still wandering as the buffer fills? |
| Pixel levels | Exposure health. The red dashed line is the clipping point — bars touching it mean saturation |

Useful flags: `--roi X Y R` to place the aperture by hand, `--split` to move the
boundary between the envelope and spin bands (default 0.5 Hz), `--headless 120`
for no window, `--no-walk` to see the raw Lomb-Scargle peak without harmonic
resolution.

---

## 4. What OBS changes — read this

**The time base becomes OBS's clock, not the phone's shutter.** Frames are
stamped with `time.perf_counter()` at the moment OpenCV hands them over, which is
after the phone has exposed, encoded, sent over USB, and OBS has composited and
re-emitted. That whole chain adds latency, and the *variation* in that latency is
jitter in your timestamps.

This is survivable for period measurement — Lomb-Scargle handles uneven sampling
natively, which is exactly why it is the right tool — and jitter of a few
milliseconds against a 1.85 s period is negligible. Watch the **jitter** figure in
the diagnostics panel. On a recorded file this machine measured 1.6 ms rms. If you
see tens of milliseconds over USB, something in the chain is struggling: drop the
resolution, drop to 30 fps, or close other applications.

**It does not fix auto-exposure — it hides it.** The software's exposure controls
(`--exposure`, the auto-exposure defeat) talk to the *virtual camera*, which
ignores them. The real exposure decision is made on the phone. If the phone's AE
is running, it responds to exactly the brightness variations you are measuring and
flattens them, and nothing downstream can detect that it happened.

The startup self-test commands two exposures and checks whether the pixels
actually move. Through OBS it will report "not working" or "inconclusive" —
that is expected and correct, because the property genuinely does nothing there.
**Lock exposure on the phone and treat that as the control.** To confirm it is
locked: wave your hand near the target and watch the light curve. If the baseline
shifts and then slowly recovers, AE is still active.

**Compression is a monotonic-ish distortion, and that is tolerable here.** OBS
encodes NV12. Period survives any monotonic transform — measured on this build,
disabling response linearisation dropped Pearson *r* from 0.9997 to 0.9574 while
the recovered period stayed accurate to 0.0095%. Amplitude-derived quantities
(panel area) are a different matter; do not quote those from an OBS stream.

---

## 5. Before you trust a number

1. **FAP below 0.01.** The big readout greys out otherwise. Pointed at a static
   scene this software will still report periods — every peak-finder does — but
   FAP climbed to 0.88 in testing and the number greyed out correctly.
2. **No saturation.** If the levels panel shows bars at the red line, shorten the
   phone's exposure. Clipped glint peaks are information destroyed.
3. **Stable period history.** A flat trace for 30+ s, not a drifting one.
4. **Clean phase fold.** One repeating profile.
5. **Sensible harmonic multiplier.** ×2 or ×4 is normal for a symmetric target —
   the light curve genuinely repeats at P/2 or P/4 and the walk climbs to the
   full period. ×1 with a smeared fold means it has not resolved anything.

The live window is a **bench aid, not the measurement**. It has no response
linearisation, no constant-frame-rate audit, and no uncertainty on the period
beyond the FAP. For anything quotable, record with `--record` and re-analyse
offline, or shoot a locked 60 fps clip and run the full pipeline:

```bash
python pipeline/run_pipeline.py --model B
python pipeline/validate.py --model B --from-photometry
```

---

## 6. A test with a known answer, before the rig exists

A record turntable at 33⅓ rpm is **1.800 s** per revolution — within 3% of the
commanded P_A of 1.84971 s, so it exercises the analysis in exactly the right
regime. 45 rpm gives 1.333 s.

Tape a strip of foil near the edge to make a specular glint, light it from off to
one side (not from beside the camera — §8.2 of the build spec explains why 30° is
better than 0°), and run:

```bash
python pipeline/live.py --source 1 --headless 90 --record data/serial/turntable.csv
```

If it returns close to 1.800 s with FAP below 0.01, the whole optical chain —
phone, OBS, aperture photometry, Lomb-Scargle, harmonic resolution — is validated
end to end against an independently known number. That is the last untested link
in the system, and it needs no motors, no printed target, and no Arduino.

A desk fan works too, but you will have no independent truth to check against.
