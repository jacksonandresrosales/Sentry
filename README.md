# 🛡️ Sentry (Centinela) - Auditoría Inteligente de Llamadas

Aplicación de escritorio para la auditoría, transcripción y análisis automatizado de llamadas grabadas en el NAS empresarial, con detección de palabras clave críticas (*"demanda"*, *"abogado"*, *"quejas"*), análisis de sentimiento y alertas en tiempo real.

---

## 🚀 Stack Tecnológico
* **Interfaz de Escritorio:** PySide6 (Qt 6)
* **Transcripción (STT):** Deepgram Nova-3 (Diarización + Enmascaramiento PII)
* **Inteligencia y Resúmenes (LLM):** Google Gemini 1.5 Flash
* **Base de Datos:** SQLite 3

---

## 📚 Documentación de Arquitectura
Para consultar el diseño detallado del sistema, el flujo de procesamiento, el esquema de base de datos y el análisis de costos, revisa el archivo:
👉 **[ARQUITECTURA.md](ARQUITECTURA.md)**

---

## 🔒 Confidencialidad
Software desarrollado para uso interno exclusivo de la empresa. Todos los derechos reservados.

---

## Ejecutar la interfaz de escritorio

Requiere Python 3.10 o superior.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.main
```

La aplicación busca archivos `.wav` y `.mp3` de forma recursiva al pulsar **Escanear carpeta**, sustituye los datos demostrativos por los audios encontrados y obtiene la duración de archivos WAV compatibles. Si existe una clave de transcripción configurada, el escaneo inicia automáticamente una cola de análisis: Deepgram u OpenAI transcriben cada audio —Deepgram se solicita en español latinoamericano (`es-419`)— y Sentry busca localmente los términos sensibles definidos. La diarización de Deepgram separa las voces y Sentry identifica localmente al asesor y al cliente por el contenido de sus frases, independientemente de quién hable primero; si no existe evidencia suficiente mantiene etiquetas neutrales. Para reducir costos, Gemini solo se consulta cuando se detecta una alerta; las llamadas normales reciben un resumen local. Gemini usa por defecto `gemini-2.5-flash-lite`, una respuesta de máximo 100 tokens y sin razonamiento adicional. Las llamadas se procesan una por una; los errores quedan visibles y no detienen las siguientes. En Configuración se pueden cambiar proveedor, modelo y clave para cada servicio. El botón **Validar** comprueba la credencial y carga modelos compatibles. Las claves permanecen en memoria durante la sesión y también pueden suministrarse mediante `DEEPGRAM_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` u `OPENAI_API_KEY`. La persistencia SQLite, el histórico y la exportación a Excel quedan para la siguiente etapa.
