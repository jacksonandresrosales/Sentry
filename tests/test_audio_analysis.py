"""Persistencia y análisis de audios con APIs simuladas: nunca consume servicios reales."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import tempfile
import time
import unittest
import wave
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from app.database import Database
from app.secret_store import protect, unprotect
from app.services.audio_analysis import (
    RESULT_SCHEMA,
    _deepgram_transcript, _gemini_analysis, _openai_analysis, _openai_transcript, analyze_file, assign_roles,
    contextual_analysis, detected_keyword_hits,
)
from app.ui.views.main_window import SentryWindow


def audio(path: Path):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0\0" * 8000)


def register(database, path):
    database.register_calls([SimpleNamespace(filename=path.name, source_path=path, duration=1)])


def config():
    return {
        "keywords": ["demanda", "abogado"],
        "transcription_provider": "deepgram", "transcription_model": "nova-3",
        "transcription_key": "clave-transcripcion-ficticia",
        "analysis_provider": "gemini", "analysis_model": "gemini-3.5-flash-lite",
        "analysis_key": "clave-analisis-ficticia",
    }


TRANSCRIPT = {
    "text": "El cliente dice que presentará una demanda con su abogado si no resuelven el problema.",
    "segments": [
        {"second": 0, "speaker": "Hablante 1", "text": "Buenos días, le atiende el asesor."},
        {"second": 5, "speaker": "Hablante 2", "text": "Presentaré una demanda con mi abogado."},
    ],
    "speaker_count": 2, "provider": "deepgram", "model": "nova-3",
}

ALERT = {
    "category": "ALERTA", "summary": "El cliente amenaza con una acción legal.",
    "sentiment": "MOLESTO", "risk": "ALTO", "validated_keywords": ["demanda", "abogado"],
    "hits": [{"keyword": "demanda", "speaker": "Hablante 2", "second": 5,
              "snippet": "Presentaré una demanda.", "validated": True}],
}


class AnalysisPersistenceTests(unittest.TestCase):
    def test_speaker_roles_use_advisor_language_not_only_first_turn(self):
        transcript = {"speaker_count": 2, "segments": [
            {"second": 0, "speaker": "Hablante 1", "text": "Tengo un problema con mi factura."},
            {"second": 3, "speaker": "Hablante 2", "text": "Buenas tardes, le atiende Carlos, permítame validar."},
            {"second": 8, "speaker": "Hablante 1", "text": "Gracias."},
        ]}
        roles = assign_roles(transcript)["segments"]
        self.assertEqual([item["speaker"] for item in roles], ["Cliente", "Asesor", "Cliente"])

    def test_keyword_phrases_create_tags_even_without_alert(self):
        transcript = {"text": "El cliente solicita cancelar el servicio.", "segments": [
            {"second": 7, "speaker": "Cliente", "text": "Quiero cancelar el servicio."}]}
        hits = detected_keyword_hits(transcript, ["cancelar el servicio", "demanda"])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["keyword"], "cancelar el servicio")
        self.assertFalse(hits[0]["validated"])

    def test_deepgram_request_uses_diarization_and_returns_speakers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audio.wav"
            audio(path)
            response = {"results": {"channels": [{"alternatives": [{"transcript": "hola respuesta"}]}],
                                    "utterances": [{"start": 0, "speaker": 0, "transcript": "hola",
                                                    "words": [{"start": 0.15}]},
                                                   {"start": 2, "speaker": 1, "transcript": "respuesta"}]}}
            with patch("app.services.audio_analysis._request", return_value=response) as request:
                transcript = _deepgram_transcript(path, "clave", "nova-3", ["demanda"])
            params = request.call_args.kwargs["params"]
            self.assertIn(("diarize_model", "latest"), params)
            self.assertNotIn(("diarize", "true"), params)
            self.assertIn(("keyterm", "demanda"), params)
            self.assertEqual(transcript["speaker_count"], 2)
            self.assertEqual(transcript["segments"][0]["word_seconds"], [0.15])

    def test_contextual_api_responses_are_parsed_as_structured_json(self):
        result = {"category": "NORMAL", "summary": "Sin novedad", "sentiment": "NEUTRAL",
                  "risk": "BAJO", "validated_keywords": []}
        gemini = {"candidates": [{"content": {"parts": [{"text": __import__("json").dumps(result)}]}}]}
        openai = {"choices": [{"message": {"content": __import__("json").dumps(result)}}]}
        with patch("app.services.audio_analysis._request", return_value=gemini) as request:
            self.assertEqual(_gemini_analysis("texto", ["demanda"], "k", "gemini-3.5-flash-lite"), result)
            generation = request.call_args.kwargs["json"]["generationConfig"]
            self.assertEqual(generation["temperature"], 0)
            self.assertEqual(generation["responseJsonSchema"], RESULT_SCHEMA)
            self.assertNotIn("responseSchema", generation)
        with patch("app.services.audio_analysis._request", return_value=openai) as request:
            self.assertEqual(_openai_analysis("texto", ["demanda"], "k", "gpt-4o-mini"), result)
            self.assertEqual(request.call_args.kwargs["json"]["response_format"]["type"], "json_schema")

    def test_openai_diarization_requests_speaker_segments(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audio.wav"
            audio(path)
            payload = {"text": "Hola, le atiende Ana. Buenas tardes.", "segments": [
                {"start": 0, "speaker": "A", "text": "Hola, le atiende Ana."},
                {"start": 2, "speaker": "B", "text": "Buenas tardes."},
            ]}
            with patch("app.services.audio_analysis._request", return_value=payload) as request:
                transcript = _openai_transcript(path, "clave", "gpt-4o-transcribe-diarize")
            self.assertEqual(request.call_args.kwargs["data"]["response_format"], "diarized_json")
            self.assertEqual(request.call_args.kwargs["data"]["chunking_strategy"], "auto")
            self.assertEqual(transcript["speaker_count"], 2)

    def test_cache_prevents_repeated_api_consumption_and_restores_results(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / "llamada.wav"
            audio(path)
            database = Database(folder / "audit.db")
            register(database, path)
            with patch("app.services.audio_analysis.transcribe", return_value=TRANSCRIPT) as transcription, \
                 patch("app.services.audio_analysis.contextual_analysis", return_value=ALERT) as analysis:
                first = analyze_file(database, path, config())
                second = analyze_file(database, path, config())
            self.assertEqual(transcription.call_count, 1)
            self.assertEqual(analysis.call_count, 1)
            self.assertEqual(first["category"], "ALERTA")
            self.assertEqual(second["hits"][0]["keyword"], "demanda")
            reopened = Database(database.path).call_rows([path])[0]
            self.assertEqual(reopened["transcript"], TRANSCRIPT["text"])
            self.assertEqual(reopened["summary"], ALERT["summary"])

    def test_failed_contextual_analysis_reuses_saved_transcription_on_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / "llamada.wav"
            audio(path)
            database = Database(folder / "audit.db")
            register(database, path)
            with patch("app.services.audio_analysis.transcribe", return_value=TRANSCRIPT) as transcription, \
                 patch("app.services.audio_analysis.contextual_analysis",
                       side_effect=[RuntimeError("corte temporal"), ALERT]) as analysis:
                with self.assertRaisesRegex(RuntimeError, "corte temporal"):
                    analyze_file(database, path, config())
                database.set_call_status(path, "ERROR", "corte temporal")
                result = analyze_file(database, path, config())
            self.assertEqual(result["category"], "ALERTA")
            self.assertEqual(transcription.call_count, 1)
            self.assertEqual(analysis.call_count, 2)

    def test_mailbox_and_normal_classification_do_not_call_contextual_api(self):
        mailbox = {"text": "Buenos días, habla asesor", "segments": [], "speaker_count": 1}
        normal = {"text": "Cliente y asesor conversan sobre el pago realizado correctamente hoy.",
                  "segments": [], "speaker_count": 2}
        with patch("app.services.audio_analysis._gemini_analysis") as api:
            self.assertEqual(contextual_analysis(mailbox, ["demanda"], "gemini", "x", "m")["category"], "BUZON")
            self.assertEqual(contextual_analysis(normal, ["demanda"], "gemini", "x", "m")["category"], "NORMAL")
            api.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "DPAPI solo está disponible en Windows")
    def test_api_key_is_encrypted_and_restored(self):
        secret = "clave-api-ficticia"
        encrypted = protect(secret)
        self.assertNotIn(secret, encrypted)
        self.assertEqual(unprotect(encrypted), secret)
        with tempfile.TemporaryDirectory() as temp:
            database = Database(Path(temp) / "audit.db")
            database.save_credential("analysis", "gemini", "gemini-3.5-flash-lite", encrypted)
            raw = database.credentials()["analysis"]["encrypted_key"]
            self.assertNotIn(secret, raw)
            self.assertEqual(unprotect(raw), secret)

    def test_interrupted_status_is_recovered_without_losing_transcript(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "audit.db"
            database = Database(path)
            fake = Path(temp) / "llamada.wav"
            audio(fake)
            register(database, fake)
            with database.connect() as connection:
                connection.execute("UPDATE calls SET status='ANALIZANDO',transcript='texto ya guardado'")
            database = Database(path)
            row = database.call_rows()[0]
            self.assertEqual(row["status"], "ERROR")
            self.assertEqual(row["transcript"], "texto ya guardado")
            self.assertIn("interrumpido", row["analysis_error"].casefold())


class AnalysisUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, window):
        deadline = time.monotonic() + 10
        while window.analysis_worker is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertIsNone(window.analysis_worker)

    def test_analyze_button_creates_three_filters_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / "llamada.wav"
            audio(path)
            window = SentryWindow(folder / "audit.db")
            try:
                window._finish_scan(folder)
                window.transcription_api_key.setText("transcripcion-ficticia")
                window.analysis_api_key.setText("analisis-ficticia")
                with patch("app.services.audio_analysis.transcribe", return_value=TRANSCRIPT), \
                     patch("app.services.audio_analysis.contextual_analysis", return_value=ALERT):
                    window.analyze_button.click()
                    self.assertEqual(window.analyze_button.text(), "Detener")
                    self.wait(window)
                self.assertEqual(window.sensitive_metric.text(), "1")
                self.assertEqual(window.call_records[0].category_code, "ALERTA")
                self.assertEqual([line.speaker for line in window.call_records[0].transcript], ["Asesor", "Cliente"])
                filter_values = {window.status_filter.itemData(i) for i in range(window.status_filter.count())}
                self.assertTrue({"alert", "mailbox", "normal"}.issubset(filter_values))
                alert_index = window.status_filter.findData("alert")
                self.assertIn("(1)", window.status_filter.itemText(alert_index))
            finally:
                window.media_player.stop()
                window.media_player.setSource(QUrl())
                window.close()
            reopened = SentryWindow(folder / "audit.db")
            try:
                self.assertEqual(reopened.call_records[0].category_code, "ALERTA")
                self.assertEqual(reopened.call_records[0].summary, ALERT["summary"])
                self.assertEqual(reopened.transcription_api_key.text(), "transcripcion-ficticia")
                self.assertEqual(reopened.analysis_api_key.text(), "analisis-ficticia")
            finally:
                reopened.media_player.stop()
                reopened.media_player.setSource(QUrl())
                reopened.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
