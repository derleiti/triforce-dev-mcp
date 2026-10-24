"""Offline regression checks for Nova's neural TTS transient retry."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from edge_tts.exceptions import NoAudioReceived

SCRIPT = Path(__file__).resolve().parents[1] / "tts.py"
spec = importlib.util.spec_from_file_location("nova_discord_tts", SCRIPT)
tts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tts)


class TestEdgeTtsRetry(unittest.IsolatedAsyncioTestCase):
    async def test_retry_preserves_voice_and_clears_partial_audio(self):
        voices = []
        attempts = []

        class FakeSpeaker:
            async def save(self, filename):
                attempts.append(filename)
                if len(attempts) == 1:
                    Path(filename).write_bytes(b"partial")
                    raise NoAudioReceived("no speech")
                self_test.assertFalse(Path(filename).exists())
                Path(filename).write_bytes(b"audio" * 800)

        self_test = self
        def communicator(text, voice, rate):
            voices.append((text, voice, rate))
            return FakeSpeaker()

        with tempfile.TemporaryDirectory() as d:
            output = str(Path(d) / "check.mp3")
            with patch.object(tts, "Communicate", side_effect=communicator), patch.object(
                tts.asyncio, "sleep", new_callable=AsyncMock
            ) as sleeping, patch.object(sys, "argv", ["tts.py", output, "Test phrase"]):
                await tts.main()
                sleeping.assert_awaited_once()
            self.assertEqual(len(attempts), 2)
            self.assertEqual(len(voices), 2)
            self.assertEqual(voices[0], voices[1])
            self.assertEqual(voices[0][1], "de-DE-SeraphinaMultilingualNeural")
            self.assertGreater(Path(output).stat().st_size, 3000)

    async def test_three_failures_are_reported_and_partial_file_removed(self):
        attempts = []

        class FailSpeaker:
            async def save(self, filename):
                attempts.append(filename)
                Path(filename).write_bytes(b"partial")
                raise NoAudioReceived("no speech")

        with tempfile.TemporaryDirectory() as d:
            output = str(Path(d) / "check.mp3")
            with patch.object(tts, "Communicate", return_value=FailSpeaker()), patch.object(
                tts.asyncio, "sleep", new_callable=AsyncMock
            ) as sleeping, patch.object(sys, "argv", ["tts.py", output, "Test phrase"]):
                with self.assertRaises(NoAudioReceived):
                    await tts.main()
                self.assertEqual(sleeping.await_count, 2)
            self.assertEqual(len(attempts), 3)
            self.assertFalse(Path(output).exists())

    async def test_invalid_text_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(sys, "argv", ["tts.py", str(Path(d) / "check.mp3"), "  "]):
                with self.assertRaises(SystemExit):
                    await tts.main()


if __name__ == "__main__":
    unittest.main()
