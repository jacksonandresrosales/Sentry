"""Recorte de silencios y protección de costes con APIs simuladas: nunca consume servicios reales."""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch

import requests

from app.services import audio_analysis
from app.services.audio_analysis import AnalysisError, transcribe
from app.services.audio_trim import trim_silence


def write_wav(path: Path, silence_before: float, speech: float, silence_after: float, *, rate=8000, channels=1):
    total = int(rate * (silence_before + speech + silence_after))
    start, end = int(rate * silence_before), int(rate * (silence_before + speech))
    samples = array("h", bytes(2 * total * channels))
    for frame in range(start, end):
        value = int(8000 * math.sin(2 * math.pi * 300 * frame / rate))
        for channel in range(channels):
            samples[frame * channels + channel] = value
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    return path


def write_mulaw(path: Path, silence_before: float, speech: float, rate=8000):
    # 0xFF es silencio en mu-law; 0x10 es una muestra fuerte.
    body = b"\xff" * int(rate * silence_before) + b"\x10\x90" * int(rate * speech / 2)
    fmt = (7).to_bytes(2, "little") + (1).to_bytes(2, "little") + rate.to_bytes(4, "little") + \
        rate.to_bytes(4, "little") + (1).to_bytes(2, "little") + (8).to_bytes(2, "little") + b"\0\0"
    chunks = b"fmt " + len(fmt).to_bytes(4, "little") + fmt + b"data" + len(body).to_bytes(4, "little") + body
    path.write_bytes(b"RIFF" + (4 + len(chunks)).to_bytes(4, "little") + b"WAVE" + chunks)
    return path


class AudioTrimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_trims_leading_and_trailing_silence_keeping_a_valid_wav(self):
        trimmed = trim_silence(write_wav(self.root / "a.wav", 5, 3, 4))
        self.assertTrue(trimmed.has_speech)
        self.assertAlmostEqual(trimmed.offset_seconds, 4.7, delta=0.05)
        self.assertLess(trimmed.kept_seconds, 4.0)
        output = self.root / "out.wav"
        output.write_bytes(trimmed.data)
        with wave.open(str(output)) as stream:
            self.assertAlmostEqual(stream.getnframes() / stream.getframerate(), trimmed.kept_seconds, delta=0.01)

    def test_stereo_and_mulaw_are_trimmed(self):
        stereo = trim_silence(write_wav(self.root / "s.wav", 3, 2, 3, channels=2))
        self.assertAlmostEqual(stereo.offset_seconds, 2.7, delta=0.05)
        mulaw = trim_silence(write_mulaw(self.root / "m.wav", 4, 2))
        self.assertAlmostEqual(mulaw.offset_seconds, 3.7, delta=0.05)
        self.assertTrue(mulaw.data.startswith(b"RIFF"))

    def test_short_gaps_are_sent_unchanged_and_silence_is_detected(self):
        unchanged = trim_silence(write_wav(self.root / "n.wav", 0.2, 3, 0.2))
        self.assertTrue(unchanged.has_speech)
        self.assertIsNone(unchanged.data)
        silent = trim_silence(write_wav(self.root / "q.wav", 3, 0, 0))
        self.assertFalse(silent.has_speech)

    def test_unsupported_formats_are_left_alone(self):
        mp3 = self.root / "a.mp3"
        mp3.write_bytes(b"ID3" + bytes(100))
        self.assertIsNone(trim_silence(mp3))
        broken = self.root / "b.wav"
        broken.write_bytes(b"not a wav")
        self.assertIsNone(trim_silence(broken))

    def test_short_quiet_and_right_channel_speech_are_preserved(self):
        for channels, amplitude, speech in ((1, 8000, 0.12), (1, 1, 0.5), (2, 8000, 0.5)):
            with self.subTest(channels=channels, amplitude=amplitude, speech=speech):
                samples = array('h', bytes(2 * 8000 * 2 * channels))
                for frame in range(8000, 8000 + int(speech * 8000)):
                    samples[frame * channels + channels - 1] = amplitude
                path = self.root / 'signal.wav'
                with wave.open(str(path), 'wb') as stream:
                    stream.setnchannels(channels)
                    stream.setsampwidth(2)
                    stream.setframerate(8000)
                    stream.writeframes(samples.tobytes())
                self.assertTrue(trim_silence(path).has_speech)

    def test_partial_final_window_and_truncated_file_are_not_discarded(self):
        path = self.root / 'last.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(8000)
            stream.writeframes(bytes(8000 * 2) + b'\x01\x00')
        self.assertTrue(trim_silence(path).has_speech)
        path.write_bytes(path.read_bytes()[:-2])
        self.assertIsNone(trim_silence(path))

    def test_multiple_audio_chunks_are_sent_without_trimming(self):
        import struct
        path = write_wav(self.root / 'multiple.wav', 3, 0, 0)
        raw = path.read_bytes() + b'data' + struct.pack('<I', 2) + b'\x01\x00'
        path.write_bytes(raw[:4] + struct.pack('<I', len(raw) - 8) + raw[8:])
        self.assertIsNone(trim_silence(path))

    def test_extensible_pcm_requires_a_complete_standard_subformat(self):
        import struct
        path = write_wav(self.root / 'extensible.wav', 3, 2, 3)
        original = path.read_bytes()
        subformat = bytes.fromhex('0100000000001000800000aa00389b71')
        for size, guid in ((40, subformat), (26, subformat[:2]), (40, subformat[:2] + bytes(14))):
            with self.subTest(size=size, guid=guid.hex()):
                fmt = struct.pack('<H', 0xfffe) + original[22:36] + struct.pack('<HHI', 22, 16, 0) + guid
                raw = b'RIFF' + bytes(4) + b'WAVEfmt ' + struct.pack('<I', size) + fmt + original[36:]
                path.write_bytes(raw[:4] + struct.pack('<I', len(raw) - 8) + raw[8:])
                result = trim_silence(path)
                if size == 40 and guid == subformat:
                    self.assertTrue(result.has_speech)
                    self.assertIsNotNone(result.data)
                else:
                    self.assertIsNone(result)


class TranscriptionCostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_silent_audio_never_reaches_the_api(self):
        path = write_wav(self.root / "silent.wav", 4, 0, 0)
        with patch.object(audio_analysis, "_request") as request:
            result = transcribe(path, "deepgram", "clave", "nova-3", ["demanda"])
        request.assert_not_called()
        self.assertEqual(result["text"], "")
        self.assertEqual(result["skipped_reason"], "sin_voz")

    def test_only_speech_is_sent_and_timestamps_match_the_original_audio(self):
        path = write_wav(self.root / "call.wav", 6, 3, 5)
        sent = {}

        def request(method, url, **kwargs):
            sent["audio"] = kwargs["data"].read()
            sent["params"] = kwargs["params"]
            return {"results": {"utterances": [{"start": 0.5, "speaker": 0, "transcript": "voy a poner una demanda",
                                                "words": [{"start": 0.5}, {"start": 1.2}]}]}}

        with patch.object(audio_analysis, "_request", side_effect=request):
            result = transcribe(path, "deepgram", "clave", "nova-3", ["demanda"])
        self.assertLess(len(sent["audio"]), path.stat().st_size / 2)
        self.assertIn(("keyterm", "demanda"), sent["params"])
        self.assertAlmostEqual(result["segments"][0]["second"], 6.2, delta=0.05)
        self.assertAlmostEqual(result["segments"][0]["word_seconds"][1], 6.9, delta=0.05)
        self.assertGreater(result["trimmed_seconds"], 9)

    def test_missing_provider_duration_uses_only_the_audio_sent(self):
        from app.database import Database
        path = write_wav(self.root / 'call.wav', 6, 3, 5)
        database = Database(self.root / 'costs.db')
        context = {'database': database, 'path': path, 'duration_seconds': 14,
                   'config': {'transcription_model': 'nova-3'}}
        token = audio_analysis._COST_CONTEXT.set(context)

        def provider(*args):
            audio_analysis._record_usage('transcription', 'deepgram', {})
            return {'text': 'hola', 'segments': []}

        try:
            with patch.object(audio_analysis, '_provider_transcript', side_effect=provider):
                result = transcribe(path, 'deepgram', 'clave', 'nova-3', [])
            self.assertIs(audio_analysis._COST_CONTEXT.get(), context)
        finally:
            audio_analysis._COST_CONTEXT.reset(token)
        with database.connect() as connection:
            billed = connection.execute('SELECT audio_seconds FROM api_usage').fetchone()[0]
        self.assertAlmostEqual(billed, 14 - result['trimmed_seconds'], places=3)

    def test_billable_upload_is_not_resent_after_an_ambiguous_disconnect(self):
        session = requests.Session()
        with patch.object(audio_analysis._HTTP, "session", session, create=True), \
             patch.object(session, "request", side_effect=requests.ReadTimeout("sin respuesta")) as call:
            with self.assertRaisesRegex(AnalysisError, "cobro doble"):
                audio_analysis._request("POST", "https://api.deepgram.com/v1/listen", billable=True, data=b"x")
        self.assertEqual(call.call_count, 1)

    def test_billable_upload_retries_when_the_connection_never_opened(self):
        session = requests.Session()
        with patch.object(audio_analysis._HTTP, "session", session, create=True), \
             patch.object(audio_analysis.time, "sleep"), \
             patch.object(session, "request", side_effect=requests.ConnectTimeout("sin conexión")) as call:
            with self.assertRaises(AnalysisError):
                audio_analysis._request("POST", "https://api.deepgram.com/v1/listen", billable=True, data=b"x")
        self.assertEqual(call.call_count, 3)

    def test_billable_upload_is_not_resent_after_server_failure(self):
        from types import SimpleNamespace
        response = SimpleNamespace(status_code=503, headers={}, json=lambda: {})
        session = requests.Session()
        with patch.object(audio_analysis._HTTP, 'session', session, create=True), \
             patch.object(session, 'request', return_value=response) as call:
            with self.assertRaisesRegex(AnalysisError, 'cobro doble'):
                audio_analysis._request('POST', 'https://api.deepgram.com/v1/listen', billable=True, data=b'x')
        self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
