#!/usr/bin/env python3
"""Persistent offline German speech recognition worker for Nova Discord voice.

Receives path names of ephemeral 16-kHz mono WAV files via stdin JSON lines.
Emits one JSON result per request; model weights stay in memory between calls.
Audio is never sent to an external transcription service by this worker.
"""
from __future__ import annotations

import json
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

MODEL_NAME = os.environ.get("NOVA_STT_MODEL", "small")
CPU_THREADS = min(8, max(2, int(os.environ.get("NOVA_STT_CPU_THREADS", "4"))))
MAX_AUDIO_BYTES = 1_600_000  # Up to ~50s 16-kHz mono PCM.
MODEL = None


def transcribe(source: str) -> tuple[str, float]:
    if not isinstance(source, str) or not source.startswith("/tmp/nova-discord-") or not source.endswith(".wav"):
        raise ValueError("Invalid temporary WAV path")
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Temporary WAV file missing or is a symlink")
    if path.stat().st_size > MAX_AUDIO_BYTES or path.stat().st_size < 5000:
        raise ValueError("WAV outside allowed size")
    with wave.open(str(path), 'rb') as wav:
        if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2:
            raise ValueError("Expected 16-kHz PCM16 mono WAV")
        data = wav.readframes(wav.getnframes())
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    segments, info = MODEL.transcribe(
        samples,
        language="de",
        task="transcribe",
        beam_size=3,
        condition_on_previous_text=False,
        vad_filter=False,
        temperature=0,
        no_speech_threshold=0.65,
        initial_prompt="Nova, hallo Nova, kannst du mich hören? Deutschsprachige Unterhaltung.",
    )
    pieces = []
    for part in segments:
        pieces.append(part.text)
        if sum(map(len, pieces)) > 800:
            break
    return " ".join(" ".join(pieces).split())[:800], float(getattr(info, "duration", 0))


def main() -> int:
    global MODEL
    started = time.monotonic()
    MODEL = WhisperModel(
        MODEL_NAME, device="cpu", compute_type="int8",
        cpu_threads=CPU_THREADS, num_workers=1,
    )
    print(json.dumps({"type": "ready", "model": MODEL_NAME, "load_ms": round((time.monotonic() - started) * 1000)}), flush=True)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            rid = int(request["id"])
            t0 = time.monotonic()
            text, duration = transcribe(request.get("path", ""))
            result = {"type": "transcript", "id": rid, "text": text,
                      "duration_s": round(duration, 2),
                      "processing_ms": round((time.monotonic() - t0) * 1000)}
        except Exception as e:
            result = {"type": "transcript", "id": request.get("id") if isinstance(request, dict) else 0,
                      "text": "", "error": type(e).__name__}
        print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
