"""Concurrencia, cuotas y caché con APIs simuladas; no usa red ni datos del usuario."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import io
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.database import Database
from app.services import audio_analysis
from app.services.analysis_batch import analysis_concurrency, run_analysis_batch
from app.services.audio_analysis import AnalysisError, analyze_file


CONFIG = {
    "keywords": ["demanda"],
    "transcription_provider": "deepgram", "transcription_model": "nova-3",
    "transcription_key": "test-no-real-key",
    "analysis_provider": "gemini", "analysis_model": "test-model",
    "analysis_key": "test-no-real-key",
}
TRANSCRIPT = {"text": "El cliente anuncia una demanda si no se resuelve su problema.",
              "segments": [], "speaker_count": 2}
RESULT = {"category": "ALERTA", "summary": "Amenaza de demanda.", "sentiment": "NEGATIVO",
          "risk": "ALTO", "validated_keywords": ["demanda"], "hits": []}


class AnalysisPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.database = Database(self.folder / "test.sqlite")

    def audios(self, count, *, identical=False):
        paths = []
        for index in range(count):
            path = self.folder / f"audio-{index}.wav"
            path.write_bytes(b"fake-audio" + (b"same" if identical else str(index).encode()))
            paths.append(path)
        self.database.register_calls([SimpleNamespace(filename=path.name, source_path=path, duration=1)
                                      for path in paths])
        return paths

    def test_single_flight_shares_requests_across_identical_files(self):
        paths = self.audios(8, identical=True)

        def transcription(*args):
            time.sleep(0.05)
            return TRANSCRIPT

        def classification(*args):
            time.sleep(0.05)
            return RESULT

        with patch.object(audio_analysis, "transcribe", side_effect=transcription) as transcribe, \
             patch.object(audio_analysis, "contextual_analysis", side_effect=classification) as classify:
            result = run_analysis_batch(self.database, paths, CONFIG, max_workers=8)
        self.assertEqual(result, (8, 0, False))
        self.assertEqual(transcribe.call_count, 1)
        self.assertEqual(classify.call_count, 1)
        self.assertEqual({row["status"] for row in self.database.call_rows()}, {"COMPLETADO"})
        self.assertFalse(audio_analysis._IN_FLIGHT)

    def test_failed_shared_request_can_be_retried_and_does_not_poison_cache(self):
        paths = self.audios(4, identical=True)

        def failed(*args):
            time.sleep(0.12)
            raise AnalysisError("error simulado")

        with patch.object(audio_analysis, "transcribe", side_effect=failed) as transcribe:
            result = run_analysis_batch(self.database, paths, CONFIG, max_workers=4)
        self.assertEqual(result, (0, 4, False))
        self.assertEqual(transcribe.call_count, 1)
        self.assertFalse(audio_analysis._IN_FLIGHT)
        with patch.object(audio_analysis, "transcribe", return_value=TRANSCRIPT) as transcribe, \
             patch.object(audio_analysis, "contextual_analysis", return_value=RESULT):
            result = run_analysis_batch(self.database, paths, CONFIG, max_workers=4)
        self.assertEqual(result, (4, 0, False))
        self.assertEqual(transcribe.call_count, 1)

    def test_changing_terms_reuses_transcript_but_changing_models_invalidates_correct_cache(self):
        path = self.audios(1)[0]
        with patch.object(audio_analysis, "transcribe", return_value=TRANSCRIPT) as transcribe, \
             patch.object(audio_analysis, "contextual_analysis", return_value=RESULT) as classify:
            analyze_file(self.database, path, CONFIG)
            changed_terms = {**CONFIG, "keywords": ["demanda", "fraude"]}
            analyze_file(self.database, path, changed_terms)
            self.assertEqual((transcribe.call_count, classify.call_count), (1, 2))
            changed_analysis = {**changed_terms, "analysis_model": "another-analysis-model"}
            analyze_file(self.database, path, changed_analysis)
            self.assertEqual((transcribe.call_count, classify.call_count), (1, 3))
            changed_transcription = {**changed_analysis, "transcription_model": "another-transcription-model"}
            analyze_file(self.database, path, changed_transcription)
            self.assertEqual((transcribe.call_count, classify.call_count), (2, 4))

    def test_starts_before_hashing_entire_batch_and_respects_concurrency(self):
        paths = self.audios(12)
        hashed = []
        first_request_hashed_count = []
        active = peak = 0
        lock = threading.Lock()
        original_hash = audio_analysis.cached_file_hash

        def hashing(database, path):
            time.sleep(0.025)
            result = original_hash(database, path)
            with lock:
                hashed.append(path)
            return result

        def transcribe(*args):
            nonlocal active, peak
            with lock:
                if not first_request_hashed_count:
                    first_request_hashed_count.append(len(hashed))
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return TRANSCRIPT

        with patch.object(audio_analysis, "cached_file_hash", side_effect=hashing), \
             patch.object(audio_analysis, "transcribe", side_effect=transcribe), \
             patch.object(audio_analysis, "contextual_analysis", return_value=RESULT):
            result = run_analysis_batch(self.database, paths, CONFIG, max_workers=3)
        self.assertEqual(result, (12, 0, False))
        self.assertLessEqual(first_request_hashed_count[0], 3)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 3)

    def test_stop_saves_running_calls_without_starting_new_ones(self):
        paths = self.audios(20)
        stop = False
        callback_threads = []
        rows = []

        def ready(row):
            nonlocal stop
            callback_threads.append(threading.get_ident())
            rows.append(row)
            stop = True

        def transcribe(*args):
            time.sleep(0.05)
            return TRANSCRIPT

        with patch.object(audio_analysis, "transcribe", side_effect=transcribe), \
             patch.object(audio_analysis, "contextual_analysis", return_value=RESULT):
            result = run_analysis_batch(self.database, paths, CONFIG, max_workers=3,
                                        row_ready=ready, stop_requested=lambda: stop)
        self.assertEqual(result, (3, 0, True))
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(callback_threads), {threading.get_ident()})
        self.assertEqual(sum(row["status"] == "COMPLETADO" for row in self.database.call_rows()), 3)

    def test_single_failure_does_not_abort_other_audio_and_preserves_base_association(self):
        paths = self.audios(3)

        def transcribe(path, *args):
            if path.name == paths[1].name:
                raise AnalysisError("simulado")
            return TRANSCRIPT

        with patch.object(audio_analysis, "transcribe", side_effect=transcribe), \
             patch.object(audio_analysis, "contextual_analysis", return_value=RESULT):
            result = run_analysis_batch(self.database, paths, {**CONFIG, "base_path": "base.xlsx"})
        self.assertEqual(result, (2, 1, False))
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM call_bases").fetchone()[0], 2)

    def test_parallelism_is_bounded(self):
        self.assertEqual([analysis_concurrency(value) for value in [0, 1, "4", 8, 999, None, "bad"]],
                         [1, 1, 4, 8, 8, 6, 6])


class ApiRetryTests(unittest.TestCase):
    def response(self, status, *, retry_after=None):
        headers = {} if retry_after is None else {"Retry-After": retry_after}
        return SimpleNamespace(status_code=status, headers=headers,
                               json=lambda: {"ok": True} if status == 200 else {"error": {"message": "busy"}})

    def test_retry_after_accepts_seconds_and_http_dates(self):
        self.assertEqual(audio_analysis._retry_after_seconds(self.response(429, retry_after="2.5")), 2.5)
        date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=10), usegmt=True)
        self.assertGreater(audio_analysis._retry_after_seconds(self.response(429, retry_after=date)), 8)
        for header in ["invalid", "nan", "inf"]:
            self.assertIsNone(audio_analysis._retry_after_seconds(self.response(429, retry_after=header)))

    def test_429_paces_shared_provider_and_rewinds_upload(self):
        stream = io.BytesIO(b"test audio")
        starts = []
        responses = iter([self.response(429, retry_after="7"), self.response(200)])

        def request(*args, **kwargs):
            starts.append(kwargs["data"].tell())
            kwargs["data"].read()
            return next(responses)

        session = Mock(request=Mock(side_effect=request))
        with patch.object(audio_analysis._HTTP, "session", session, create=True), \
             patch.object(audio_analysis, "_wait_for_provider") as wait_provider, \
             patch.object(audio_analysis, "_defer_provider") as defer_provider, \
             patch.object(audio_analysis.time, "sleep") as sleep:
            payload = audio_analysis._request("POST", "https://api.example.test/audio", data=stream)
        self.assertTrue(payload["ok"])
        self.assertEqual(starts, [0, 0])
        self.assertEqual(wait_provider.call_count, 2)
        defer_provider.assert_called_once_with("https://api.example.test/audio", 7)
        sleep.assert_not_called()

    def test_exhausted_retry_does_not_loop_forever(self):
        session = Mock(request=Mock(return_value=self.response(503)))
        with patch.object(audio_analysis._HTTP, "session", session, create=True), \
             patch.object(audio_analysis, "_wait_for_provider"), \
             patch.object(audio_analysis.time, "sleep") as sleep:
            with self.assertRaisesRegex(AnalysisError, "503"):
                audio_analysis._request("POST", "https://api.example.test/audio")
        self.assertEqual(session.request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_provider_cooldown_does_not_hold_network_lock_and_is_origin_scoped(self):
        with patch.dict(audio_analysis._PROVIDER_COOLDOWNS, {}, clear=True), \
             patch.object(audio_analysis.time, "monotonic", side_effect=[100, 100, 101, 102]), \
             patch.object(audio_analysis.time, "sleep") as sleep:
            audio_analysis._defer_provider("https://api.one.test/listen", 2)
            audio_analysis._wait_for_provider("https://api.two.test/analyze")
            audio_analysis._wait_for_provider("https://api.one.test/other")
        sleep.assert_called_once_with(1.0)


if __name__ == "__main__":
    unittest.main()
