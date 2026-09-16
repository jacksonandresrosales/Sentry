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

La funcionalidad también está integrada en Sentry: abre `Sentry.cmd` y pulsa **Bases**, en el header junto al directorio. Permite cargar una o varias bases, consolidarlas, elegir carpeta, configurar V/R y número de base, usar opciones avanzadas y abrir el Excel o su carpeta. Conserva el modelo NO y la numeración sin sobrescrituras. El procesamiento trabaja en segundo plano y el historial permanece al reiniciar. Al terminar, **Usar base para buscar audios** toma teléfono y fecha de `Hoja1`: solo incorpora WAV/MP3 `q-<cola>-<teléfono>-<AAAAMMDD>-...` cuando coinciden ambos valores. La selección también puede hacerse desde una ejecución del historial y se conserva entre sesiones.

## Base de datos local

Se crea automáticamente en `data/db/sentry_audit.db`. Incluye `calls`, `keyword_hits`, `app_settings`, `base_jobs`, `base_records` y la configuración única `remote_connection`. Guarda los audios detectados, ajustes no secretos, ejecuciones y registros únicos procesados; mantiene teléfonos y documentos como texto. La aplicación inicia sin llamadas ficticias y recupera los análisis reales guardados. Las claves API y la contraseña SFTP se almacenan cifradas mediante DPAPI.

Para preparar el esquema sin abrir la interfaz:

```powershell
python -m app.database
```

La base SQLite y los Excel de `outputs` son privados y no se suben a Git. Contienen información sensible: deben guardarse en un equipo y carpetas con acceso restringido. El SQLite no está cifrado; no sustituye los controles de acceso del equipo. Para respaldarlo, cierra Sentry y copia `data/db/sentry_audit.db`, además de los Excel si quieres conservarlos.

## 🔒 Confidencialidad

Software desarrollado para uso interno exclusivo de la empresa. Todos los derechos reservados.

---

## Ejecutar la interfaz de escritorio

Sentry está preparado para Windows 10/11 y requiere Python 3.10 o superior. En un equipo nuevo, descarga el repositorio y haz doble clic en `Instalar_Sentry.cmd`: crea un entorno aislado, instala las dependencias e inicializa la base SQLite. Después se abre normalmente con `Sentry.cmd`.

Para buscar en carpetas locales o NAS no hace falta instalar otro componente. La conexión con Issabel requiere [WinSCP](https://winscp.net/eng/download.php) con `WinSCPnet.dll`; se admite la instalación para todos los usuarios y la instalación local. Cada equipo debe introducir sus propias credenciales y claves API desde **Configuración**, ya que se cifran con el usuario de Windows y nunca se distribuyen mediante Git.

Instalación manual equivalente:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.main
```

En **Configuración → Directorio de grabaciones** se elige **Carpeta local**, **NAS / carpeta compartida** o **Issabel / SFTP**. Local y NAS buscan en la ruta elegida y, cuando existe una estructura por fecha, entran directamente en `año/mes/día`. Issabel filtra primero en el servidor por las fechas y teléfonos de la base, descarga únicamente las coincidencias a `data/remote_audio` y reutiliza esas copias en búsquedas posteriores. Los archivos originales del servidor nunca se eliminan. **Analizar** transcribe los audios resultantes y los separa en **Demandas / alertas**, **Buzones** y **Llamadas normales**. A la derecha de **Llamadas detectadas** se puede filtrar por clasificación y ordenar por prioridad de términos, duración, fecha, nombre u orden original. La diarización distingue las voces y Sentry usa localmente expresiones habituales de atención para rotularlas como **Asesor** y **Cliente**, sin una consulta adicional a la IA. Cada palabra o frase configurada que aparezca se añade como etiqueta visible y buscable en la llamada.

Los recorridos de carpetas locales y NAS se ejecutan en segundo plano para mantener la ventana disponible. La cola crea visualmente solo las filas necesarias conforme se desplaza, SQLite agrupa la lectura de llamadas y etiquetas, y la reproducción actualiza únicamente la línea activa de la transcripción. Sentry también conserva la huella de cada archivo junto con su tamaño y fecha de modificación: si el audio no cambió, evita volver a leerlo completo antes de consultar las cachés.

La transcripción acompaña la reproducción y desplaza automáticamente la línea activa. Cuando el proveedor entrega tiempos por palabra, el texto progresa con esas marcas exactas; los análisis anteriores usan una interpolación local sin consumir nuevamente la API. También se puede pulsar cualquier bloque para mover el audio a ese segundo, y **Ir al momento** centra tanto la evidencia escrita como el audio.

Se guarda cada llamada apenas termina; al reiniciar se recuperan lista, transcripción, resumen, categoría, etiquetas, evidencias y estado de revisión. Los trabajos interrumpidos quedan marcados para reintentar.

Para reducir consumo, Sentry calcula una huella SHA-256 y conserva cachés separadas de transcripción y análisis: repetir el botón con el mismo audio, modelos y términos no vuelve a llamar a las APIs. Si no hay términos sensibles, la clasificación normal se hace localmente y no consume la API contextual; esta solo recibe fragmentos cercanos a posibles coincidencias. Un audio sin conversación o con un único hablante detectado se clasifica como buzón.

Las alertas detectadas por términos sensibles aparecen como denuncias automáticas. El botón **Marcar como verificada** confirma o revierte esa clasificación manual sin perderla al cerrar la aplicación. Desde **Exportar Excel** se pueden generar archivos de denuncias automáticas pendientes, denuncias verificadas, todas las denuncias o toda la base activa. La salida contiene únicamente número de celular, nombre del cliente, ID y estado; las denuncias se resaltan en rojo y los demás registros en verde.

En **Configuración** se cambian proveedor, modelo y claves. **Validar** comprueba la credencial y carga modelos. Al guardar ajustes, al validar o al cerrar Sentry, las claves se cifran mediante la protección DPAPI de Windows y se almacenan en SQLite; no quedan en texto plano y solo el mismo usuario de Windows puede recuperarlas. También pueden suministrarse mediante `DEEPGRAM_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY` u `OPENAI_API_KEY`.

En **Configuración → Apariencia** se puede alternar inmediatamente entre el tema claro y **Oscuro · Zinc / Noche**. La selección queda guardada para el siguiente inicio y Sentry mantiene una paleta propia, independiente del modo de Windows.

En esa misma vista, **Servidor de grabaciones (WinSCP / SFTP)** solicita únicamente los mismos datos del inicio de sesión habitual: IP, usuario y contraseña. La raíz recomendada es `/var/spool/asterisk/monitor/`, sin fijar el año. **Conectar servidor** utiliza el puerto SFTP estándar 22, obtiene la huella SSH, comprueba el acceso a la carpeta inicial y guarda la contraseña cifrada para el usuario actual de Windows. Tras conectarse, **Buscar audios en Issabel** admite un teléfono o parte del nombre; con una base activa limita la consulta a sus carpetas de fecha en `/var/spool/asterisk/monitor/<año>/<mes>/<día>/`, evitando recorrer el año completo y cambiando de año automáticamente según `Hoja1`. La contraseña viaja al proceso local de WinSCP mediante una variable de entorno temporal, nunca como argumento de consola ni dentro de los registros de Sentry.

La transformación de bases y su Excel siguen disponibles desde **Bases**. Reportes consulta exclusivamente el historial global de SQLite, independientemente del directorio activo. Permite elegir un día, una semana (lunes a domingo) o un mes calendario y una fecha de referencia. Muestra llamadas analizadas, incidentes, denuncias verificadas, bases, actividad diaria y palabras frecuentes; incluye registros y exportación a Excel. Un incidente es una llamada clasificada como alerta. Cada llamada cuenta una vez según su último análisis, en hora local; no es un historial de cada reintento. La relación llamada/base se registra a partir de esta versión y no se infiere para registros anteriores. Las bases preparadas se presentan por separado de las bases con llamadas analizadas. Si el período no contiene registros, Reportes muestra un estado vacío. Las tablas muestran hasta 500 filas y el Excel exporta todas.

Pruebas automatizadas con archivos y bases temporales:

```powershell
python -m unittest discover -s tests -v
```
