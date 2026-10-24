"""Generate one MP3 via the German Edge neural voice without exposing credentials."""
import asyncio
import os
import sys
from pathlib import Path

from edge_tts import Communicate
from edge_tts.exceptions import NoAudioReceived

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
    # The Edge speech gateway occasionally delivers an empty audio stream.
    # Retry the same neural voice rather than silently switching speakers.
    for attempt in range(3):
        try:
            await Communicate(text, voice=VOICE, rate=RATE).save(str(dest))
            if dest.stat().st_size < 3000:
                raise RuntimeError("TTS output empty")
            return
        except NoAudioReceived:
            dest.unlink(missing_ok=True)
            if attempt == 2:
                raise
            await asyncio.sleep(0.75 * (attempt + 1))


if __name__ == "__main__":
    asyncio.run(main())
