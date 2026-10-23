"""Generate one MP3 via the German Edge neural voice without exposing credentials."""
import asyncio
import os
import sys
from pathlib import Path

from edge_tts import Communicate

VOICE = os.getenv("NOVA_TTS_VOICE", "de-DE-SeraphinaMultilingualNeural")
RATE = os.getenv("NOVA_TTS_RATE", "-4%")


async def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: tts.py OUTPUT.mp3 TEXT")
    dest = Path(sys.argv[1]).resolve()
    text = sys.argv[2].strip()
    if not text or len(text) > 600:
        raise SystemExit("Text must be 1..600 characters")
    if dest.suffix != ".mp3":
        raise SystemExit("Output must be .mp3")
    dest.parent.mkdir(parents=True, exist_ok=True)
    await Communicate(text, voice=VOICE, rate=RATE).save(str(dest))
    if dest.stat().st_size < 3000:
        raise RuntimeError("TTS output empty")


if __name__ == "__main__":
    asyncio.run(main())
