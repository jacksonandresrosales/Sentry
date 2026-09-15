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

## Transformación de bases de llamadas

El script [scripts/transformar_base.py](scripts/transformar_base.py) convierte bases CSV o Excel al formato NO, con clasificación de cédula/RUC y consolidación sin teléfonos repetidos. Consulta instalación, uso y reglas en [scripts/README.md](scripts/README.md).

En Windows, haz doble clic en `DB_delete.cmd` para abrir la mini aplicación, cargar tu base y abrir el resultado o la carpeta de salida.

La funcionalidad también está integrada en Sentry: abre `Sentry.cmd` y pulsa **Bases**, en el header junto al directorio. Permite cargar una o varias bases, consolidarlas, elegir carpeta, configurar V/R y número de base, usar opciones avanzadas y abrir el Excel o su carpeta. Conserva el modelo NO y la numeración sin sobrescrituras. El procesamiento trabaja en segundo plano y el historial permanece al reiniciar.

## Base de datos local

Se crea automáticamente en `data/db/sentry_audit.db`. Incluye `calls`, `keyword_hits`, `app_settings`, `base_jobs` y `base_records`. Guarda los audios detectados, ajustes no secretos, ejecuciones y registros únicos procesados; mantiene teléfonos y documentos como texto. Las llamadas de demostración no se guardan. Las claves API siguen solo en memoria.

Para preparar el esquema sin abrir la interfaz:

```powershell
python -m app.database
```

La base SQLite y los Excel de `outputs` son privados y no se suben a Git. Contienen información sensible: deben guardarse en un equipo y carpetas con acceso restringido. El SQLite no está cifrado; no sustituye los controles de acceso del equipo. Para respaldarlo, cierra Sentry y copia `data/db/sentry_audit.db`, además de los Excel si quieres conservarlos.

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

El botón **Escanear carpeta** busca archivos `.wav` y `.mp3` de forma recursiva y obtiene la duración de archivos WAV compatibles. Deepgram u OpenAI pueden transcribir cada audio; la separación de voces y el contenido de sus frases permiten distinguir al asesor del cliente. Los términos sensibles se buscan localmente y Gemini solo se consulta cuando existe una posible alerta, reduciendo consumo. Los audios se procesan uno por uno y los errores no detienen la cola. En Configuración se pueden cambiar proveedor, modelo y clave para cada servicio. La transformación de bases y su exportación Excel están conectadas a SQLite desde **Bases**; los reportes agregados siguen siendo demostrativos.

Pruebas con CSV ficticios, SQLite temporal e interfaz sin pantalla:

```powershell
python -m unittest discover -s tests -v
```
