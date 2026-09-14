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

La versión actual es un prototipo funcional de interfaz con datos demostrativos. En Configuración se pueden cambiar proveedor, modelo y clave para transcripción y análisis. El botón **Validar** comprueba la credencial contra el proveedor seleccionado, carga sus modelos disponibles y conserva un modelo compatible seleccionado. Las claves permanecen en memoria durante la sesión y también pueden suministrarse mediante `DEEPGRAM_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` u `OPENAI_API_KEY`. Las integraciones de procesamiento con NAS, Deepgram, Gemini, OpenAI, SQLite y Excel se conectarán en etapas posteriores.
