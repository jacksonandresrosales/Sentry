"""Reconsidera buzones con términos sensibles usando únicamente APIs simuladas."""
from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.database import Database
from app.services import audio_analysis
from app.services.audio_analysis import AnalysisError, analyze_file, contextual_analysis


CONFIG = {
    "keywords": ["demanda"],
    "transcription_provider": "deepgram", "transcription_model": "nova-3",
    "transcription_key": "test-no-real-key",
    "analysis_provider": "gemini", "analysis_model": "test-model",
    "analysis_key": "test-no-real-key",
}


def transcript(text, speakers=1):
    return {"text": text, "speaker_count": speakers,
            "segments": [{"second": 2.5, "speaker": "Hablante 1", "text": text}]}


def alert(keyword):
    return {"category": "ALERTA", "summary": "El hablante anuncia una denuncia.",
            "sentiment": "NEGATIVO", "risk": "ALTO", "validated_keywords": [keyword]}


class MailboxContextTests(unittest.TestCase):
    def test_single_speaker_complaint_is_validated_and_can_be_alert(self):
        speech = transcript("Voy a presentar una denuncia si no resuelven este problema.")
        with patch.object(audio_analysis, "_gemini_analysis", return_value=alert("denuncia")) as api:
            result = contextual_analysis(speech, ["denuncia", "fraude"], "gemini", "key", "model")
        self.assertEqual(result["category"], "ALERTA")
        self.assertEqual(result["risk"], "ALTO")
        self.assertEqual(result["hits"][0]["second"], 2.5)
        self.assertTrue(result["hits"][0]["validated"])
        self.assertEqual(api.call_args.args[1], ["denuncia"])
        self.assertIn(speech["text"], api.call_args.args[0])

    def test_short_complaint_is_validated_even_with_fewer_than_five_words(self):
        for speakers in (None, 1, 2):
            with self.subTest(speakers=speakers):
                with patch.object(audio_analysis, "_openai_analysis", return_value=alert("denunciar")) as api:
                    result = contextual_analysis(transcript("Voy a denunciar", speakers),
                                                 ["denunciar"], "openai", "key", "model")
                self.assertEqual(result["category"], "ALERTA")
                self.assertTrue(result["hits"][0]["validated"])
                api.assert_called_once()

    def test_context_without_risk_preserves_mailbox_and_unvalidated_hits(self):
        normal = {"category": "NORMAL", "summary": "Mención comercial sin riesgo.",
                  "sentiment": "POSITIVO", "risk": "BAJO", "validated_keywords": ["abogado"]}
        for speech in (transcript("Somos un servicio de abogado que ofrece asesorías legales."),
                       transcript("Abogado a disposición", 2)):
            with self.subTest(text=speech["text"]):
                with patch.object(audio_analysis, "_gemini_analysis", return_value=normal) as api:
                    result = contextual_analysis(speech, ["abogado"], "gemini", "key", "model")
                api.assert_called_once()
                self.assertEqual(result["category"], "BUZON")
                self.assertEqual(result["summary"], "No se detectó una conversación entre cliente y asesor.")
                self.assertEqual((result["sentiment"], result["risk"]), ("NEUTRAL", "BAJO"))
                self.assertEqual(result["validated_keywords"], [])
                self.assertEqual(result["hits"][0]["keyword"], "abogado")
                self.assertFalse(result["hits"][0]["validated"])

    def test_no_candidate_keeps_no_api_fast_path(self):
        with patch.object(audio_analysis, "_gemini_analysis") as gemini, \
             patch.object(audio_analysis, "_openai_analysis") as openai:
            for speech in (transcript("Deje su mensaje después de la señal."), transcript("", None),
                           transcript("Llame luego", 2)):
                self.assertEqual(contextual_analysis(speech, ["denuncia"], "gemini", "key", "model")["category"], "BUZON")
            normal = contextual_analysis(transcript("Cliente y asesor hablan de una oferta comercial disponible.", 2),
                                         ["denuncia"], "openai", "key", "model")
            self.assertEqual(normal["category"], "NORMAL")
        gemini.assert_not_called()
        openai.assert_not_called()

    def test_invalid_validation_does_not_silently_keep_mailbox(self):
        with patch.object(audio_analysis, "_gemini_analysis", return_value={"category": "OTRO"}):
            with self.assertRaisesRegex(AnalysisError, "categoría inválida"):
                contextual_analysis(transcript("Voy a denunciar"), ["denunciar"], "gemini", "key", "model")


class MailboxReanalysisTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.path = folder / "ficticio.wav"
        self.path.write_bytes(b"audio ficticio: nunca se envia a una API")
        self.database = Database(folder / "test.sqlite")
        self.database.register_calls([SimpleNamespace(filename=self.path.name, source_path=self.path, duration=5)])

    def test_new_keywords_reconsider_completed_mailbox_without_retranscription(self):
        speech = transcript("Denunciaré este fraude porque nadie ha resuelto mi problema.")
        with patch.object(audio_analysis, "transcribe", return_value=speech) as transcribe, \
             patch.object(audio_analysis, "_gemini_analysis", return_value=alert("fraude")) as api:
            first = analyze_file(self.database, self.path, CONFIG)
            self.assertEqual(first["category"], "BUZON")
            api.assert_not_called()
            updated = {**CONFIG, "keywords": ["demanda", "fraude"]}
            second = analyze_file(self.database, self.path, updated)
            third = analyze_file(self.database, self.path, updated)
        self.assertEqual(transcribe.call_count, 1)
        self.assertEqual(api.call_count, 1)
        self.assertEqual(second["category"], "ALERTA")
        self.assertEqual(third["category"], "ALERTA")
        self.assertNotEqual(first["analysis_terms"], second["analysis_terms"])
        self.assertEqual(second["transcript"], speech["text"])
        self.assertEqual(second["hits"][0]["keyword"], "fraude")
        self.assertEqual(second["hits"][0]["is_risk_validated"], 1)

    def test_new_analysis_version_invalidates_old_mailbox_but_reuses_transcript(self):
        speech = transcript("Voy a presentar una demanda porque no me han dado ninguna solución.")
        old_result = {"category": "BUZON", "summary": "Clasificación antigua.", "sentiment": "NEUTRAL",
                      "risk": "BAJO", "validated_keywords": [], "hits": []}
        with patch.object(audio_analysis, "transcribe", return_value=speech) as transcribe:
            with patch.object(audio_analysis, "ANALYSIS_VERSION", "3"), \
                 patch.object(audio_analysis, "contextual_analysis", return_value=old_result):
                first = analyze_file(self.database, self.path, CONFIG)
            with patch.object(audio_analysis, "_gemini_analysis", return_value=alert("demanda")) as api:
                second = analyze_file(self.database, self.path, CONFIG)
        self.assertEqual(first["category"], "BUZON")
        self.assertEqual(second["category"], "ALERTA")
        self.assertNotEqual(first["cache_key"], second["cache_key"])
        self.assertEqual(transcribe.call_count, 1)
        self.assertEqual(api.call_count, 1)
        with self.database.connect() as connection:
            timing = connection.execute("SELECT transcription_cached,analysis_cached FROM analysis_timings").fetchone()
            self.assertEqual(tuple(timing), (1, 0))
            self.assertEqual(connection.execute("SELECT count(*) FROM transcription_cache").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
