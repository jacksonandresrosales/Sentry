# 🛡️ Sentry (Centinela) - Auditoría Inteligente de Llamadas

Aplicación de escritorio para la auditoría, transcripción y análisis automatizado de llamadas grabadas en el NAS empresarial, con detección de palabras clave críticas (*"demanda"*, *"abogado"*, *"quejas"*), análisis de sentimiento y alertas en tiempo real.

---

## 🚀 Stack Tecnológico
* **Interfaz de Escritorio:** PySide6 (Qt 6)
* **Transcripción (STT):** Deepgram Nova-3 u OpenAI GPT-4o Transcribe Diarize
* **Inteligencia y Resúmenes (LLM):** Google Gemini Flash-Lite u OpenAI
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

Se crea automáticamente en `data/db/sentry_audit.db`. Incluye `calls`, `keyword_hits`, `app_settings`, `base_jobs` y `base_records`. Guarda los audios detectados, ajustes no secretos, ejecuciones y registros únicos procesados; mantiene teléfonos y documentos como texto. La aplicación inicia sin llamadas ficticias y recupera los análisis reales guardados. Las claves API se almacenan cifradas mediante DPAPI.

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

El botón **Escanear carpeta** busca `.wav` y `.mp3` de forma recursiva y los registra en SQLite. **Analizar** transcribe los audios y los separa en **Demandas / alertas**, **Buzones** y **Llamadas normales**. A la derecha de **Llamadas detectadas** se puede filtrar por clasificación y ordenar por prioridad de términos, duración, fecha, nombre u orden original. La diarización distingue las voces y Sentry usa localmente expresiones habituales de atención para rotularlas como **Asesor** y **Cliente**, sin una consulta adicional a la IA. Cada palabra o frase configurada que aparezca se añade como etiqueta visible y buscable en la llamada.

La transcripción acompaña la reproducción y desplaza automáticamente la línea activa. Cuando el proveedor entrega tiempos por palabra, el texto progresa con esas marcas exactas; los análisis anteriores usan una interpolación local sin consumir nuevamente la API. También se puede pulsar cualquier bloque para mover el audio a ese segundo, y **Ir al momento** centra tanto la evidencia escrita como el audio.

Se guarda cada llamada apenas termina; al reiniciar se recuperan lista, transcripción, resumen, categoría, etiquetas, evidencias y estado de revisión. Los trabajos interrumpidos quedan marcados para reintentar.

Para reducir consumo, Sentry calcula una huella SHA-256 y conserva cachés separadas de transcripción y análisis: repetir el botón con el mismo audio, modelos y términos no vuelve a llamar a las APIs. Si no hay términos sensibles, la clasificación normal se hace localmente y no consume la API contextual; esta solo recibe fragmentos cercanos a posibles coincidencias. Un audio sin conversación o con un único hablante detectado se clasifica como buzón.

En **Configuración** se cambian proveedor, modelo y claves. **Validar** comprueba la credencial y carga modelos. Al guardar ajustes, al validar o al cerrar Sentry, las claves se cifran mediante la protección DPAPI de Windows y se almacenan en SQLite; no quedan en texto plano y solo el mismo usuario de Windows puede recuperarlas. También pueden suministrarse mediante `DEEPGRAM_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` u `OPENAI_API_KEY`.

La transformación de bases y su Excel siguen disponibles desde **Bases**. Los reportes muestran únicamente conteos reales del directorio analizado; si no hay grabaciones presentan un estado vacío.

Pruebas con CSV ficticios, SQLite temporal e interfaz sin pantalla:

```powershell
python -m unittest discover -s tests -v
```
