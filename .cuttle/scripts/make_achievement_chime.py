#!/usr/bin/env python3
"""Generate src/web/sounds/achievement-unlock.wav.

A short ascending three-note chime (C6 → E6 → G6) with a bell-ish harmonic
stack and exponential decay, plus a fifth sparkle at the end. Written with the
stdlib only (wave + math) so it can be regenerated on any machine without
numpy or an audio toolchain.

    .venv/bin/python .cuttle/scripts/make_achievement_chime.py

Re-run this whenever the timbre changes, then copy the result into
electron/assets/ (that copy is checked in and must stay in sync — see
electron/main.js resolveSfxWavPath).
"""

from __future__ import annotations

import math
import os
import struct
import wave

SAMPLE_RATE = 44100
OUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "src", "web", "sounds", "achievement-unlock.wav",
)

# (start seconds, frequency Hz, gain, decay seconds)
NOTES = [
    (0.00, 1046.50, 0.50, 0.55),   # C6
    (0.09, 1318.51, 0.46, 0.60),   # E6
    (0.18, 1567.98, 0.44, 0.80),   # G6
    (0.30, 2093.00, 0.24, 1.05),   # C7 sparkle
]

# Bell-ish partials: (harmonic number, relative gain)
PARTIALS = [(1, 1.0), (2, 0.42), (3, 0.20), (4, 0.11), (5.06, 0.05)]

TAIL = 0.25  # extra seconds of decay after the last note starts
DURATION = max(start + decay for start, _, _, decay in NOTES) + TAIL


def _envelope(t: float, decay: float) -> float:
    """Fast attack, exponential decay, 8ms fade-in to avoid a click."""
    if t < 0.0:
        return 0.0
    attack = min(1.0, t / 0.008)
    return attack * math.exp(-t / decay)


def render() -> bytes:
    total = int(SAMPLE_RATE * DURATION)
    samples = [0.0] * total
    for start, freq, gain, decay in NOTES:
        begin = int(start * SAMPLE_RATE)
        for n in range(begin, total):
            t = (n - begin) / SAMPLE_RATE
            env = _envelope(t, decay)
            if env < 1e-4:
                break
            value = 0.0
            for harmonic, weight in PARTIALS:
                value += weight * math.sin(2.0 * math.pi * freq * harmonic * t)
            samples[n] += value * env * gain
    peak = max(1e-9, max(abs(s) for s in samples))
    # Normalise to -1.5 dBFS so the chime is audible over the reply chirp.
    scale = 0.84 / peak
    frames = bytearray()
    for s in samples:
        v = int(max(-1.0, min(1.0, s * scale)) * 32767)
        frames += struct.pack("<h", v)
    return bytes(frames)


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    data = render()
    with wave.open(OUT, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        fh.writeframes(data)
    print(f"wrote {OUT} ({len(data)} bytes, {DURATION:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())