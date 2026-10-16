<p align="center">
  <img src="app/ui/assets/sentry-logo.png" alt="Logo de Sentry" width="280">
</p>

<h1 align="center">Sentry</h1>

<p align="center">
  Auditoría inteligente de llamadas para equipos que necesitan encontrar, entender y verificar evidencias de atención al cliente.
</p>

<p align="center">
  <a href="https://github.com/jacksonandresrosales/Sentry/releases"><img src="https://img.shields.io/github/v/release/jacksonandresrosales/Sentry?label=release&color=27953c" alt="Última release"></a>
  <a href="https://github.com/jacksonandresrosales/Sentry/releases"><img src="https://img.shields.io/badge/estado-estable-27953c" alt="Estado estable"></a>
  <img src="https://img.shields.io/badge/Windows-10%2F11-0078D6?logo=windows&logoColor=white" alt="Windows 10 y 11">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10 o superior">
  <img src="https://img.shields.io/badge/Qt-PySide6-41CD52?logo=qt&logoColor=white" alt="PySide6">
  <img src="https://img.shields.io/badge/base%20local-SQLite-003B57?logo=sqlite&logoColor=white" alt="SQLite">
</p>

<p align="center">
  <a href="ARQUITECTURA.md">Arquitectura</a> ·
  <a href="SPECS.md">Especificaciones</a> ·
  <a href="https://github.com/jacksonandresrosales/Sentry/issues">Reportar un problema</a>
</p>

<p align="center">
  <a href="https://github.com/jacksonandresrosales/Sentry/releases/latest/download/Sentry_Setup.exe"><img src="https://img.shields.io/badge/Descargar-Sentry%20para%20Windows-27953c?style=for-the-badge&logo=windows&logoColor=white" alt="Descargar Sentry para Windows"></a>
</p>

---

## Qué es Sentry

Sentry es una aplicación de escritorio para Windows que centraliza la auditoría de grabaciones telefónicas. Busca audios en carpetas locales, unidades NAS o Issabel, los relaciona con bases de clientes y permite revisar cada caso con transcripción, análisis contextual, reproducción sincronizada y reportes.

La aplicación está orientada al uso interno de Ecuaconexión. Puede procesar información sensible, por lo que las credenciales, bases, grabaciones y reportes deben mantenerse en equipos y carpetas con acceso restringido.

> **Versión actual:** `1.0.1`

## Funcionalidades

- Búsqueda en carpetas locales, NAS e Issabel mediante SFTP.
- Asociación de llamadas con clientes por teléfono y fecha.
- Transcripción con Deepgram Nova-3, AssemblyAI Universal-2 o AssemblyAI Universal-3.5 Pro.
- Análisis contextual con Gemini 3.5 Flash, DeepSeek V4.1 Flash o Groq GPT-OSS 120B.
- Clasificación en alertas, buzones y llamadas normales.
- Detección y etiquetado de palabras o frases sensibles.
- Reproducción de audio sincronizada con la transcripción.
- Verificación manual de denuncias, llamadas normales y buzones.
- Reevaluación automática al actualizar palabras o frases sensibles.
- Transformación y consolidación de bases CSV y Excel.
- Historial persistente y reportes por día, semana o mes.
- Exportación de resultados a Excel.
- Los menús desplegables solo cambian con un clic; la rueda del mouse no altera la selección.
- Temas claro y oscuro independientes de Windows.
- Actualizaciones verificadas desde la propia aplicación.

## Instalación recomendada

Pulsa **Descargar** arriba y ejecuta el instalador. Incluye Python, Qt, WinSCP y las dependencias necesarias.

La instalación:

- crea accesos directos en el menú Inicio y, opcionalmente, en el escritorio;
- inicializa una base de datos vacía;
- conserva los datos del usuario fuera de la carpeta del programa;
- permite configurar las claves API y las conexiones desde **Configuración**.

## Actualizaciones

Desde beta.6, Sentry puede consultar las nuevas publicaciones del repositorio desde **Configuración → Actualizaciones de Sentry**. La aplicación comprueba versión, tamaño y huella SHA-256 antes de habilitar la instalación, crea un respaldo de SQLite y solo reinicia después de la confirmación del usuario.

Desde beta.8, **Acerca de Sentry** obtiene de GitHub las notas oficiales de la versión instalada y permite consultar el historial de publicaciones anteriores. Si no hay conexión, muestra la información incluida con la aplicación.

Las actualizaciones reemplazan el programa, pero conservan la base, los resultados, las grabaciones descargadas y la configuración en `%LOCALAPPDATA%\Ecuaconexion\Sentry`. Las versiones beta y estables se administran por separado.

## Inicio desde el código fuente

Requiere Windows 10/11 de 64 bits y Python 3.10 o superior.

En Windows puedes utilizar el instalador de desarrollo:

```powershell
.\Instalar_Sentry.cmd
```

Después inicia la aplicación con `Sentry.vbs`. La instalación manual equivalente es:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.main
```

## Configuración

Desde **Configuración** se seleccionan de forma independiente el proveedor y el modelo de transcripción y de análisis.

| Servicio | Variables de entorno admitidas |
| --- | --- |
| Deepgram Nova-3 | `DEEPGRAM_API_KEY` |
| AssemblyAI Universal-2 / Universal-3.5 Pro | `ASSEMBLYAI_API_KEY` (proveedor inicial con `SENTRY_TRANSCRIPTION_PROVIDER=assemblyai`) |
| Google Gemini 3.5 Flash | `GEMINI_API_KEY`, `GOOGLE_API_KEY` |
| DeepSeek V4.1 Flash | `DEEPSEEK_API_KEY` |
| Groq GPT-OSS 120B | `GROQ_API_KEY` |

Los modelos se fijan a las opciones admitidas para evitar fallos de compatibilidad. Para transcripción se elige **Deepgram** (Nova-3) o **AssemblyAI**, que permite escoger Universal-2 o Universal-3.5 Pro. Sentry recuerda la última clave validada de cada proveedor, así que se puede alternar entre ellos para comparar sin volver a escribirla. AssemblyAI recibe los términos sensibles como Keyterms Prompting (hasta 200 en Universal-2 y 1000 en Universal-3.5 Pro, de máximo seis palabras). Después de guardar la transcripción en la caché local, Sentry intenta eliminar la copia de la cuenta de AssemblyAI; si esa petición falla, la copia remota puede permanecer. Para análisis se puede elegir Gemini 3.5 Flash, DeepSeek V4.1 Flash (`deepseek-flash`) o **Otros proveedores → Groq** (`openai/gpt-oss-120b`). Groq y DeepSeek se usan aquí solo para análisis. La cuenta gratuita de Groq tiene límites de solicitudes y tokens; ante un lote grande, ajusta la concurrencia en Configuración.

Las claves guardadas desde la interfaz se protegen mediante DPAPI y solo pueden recuperarse con el mismo usuario de Windows.

### Fuentes de grabaciones

#### Local o NAS

Sentry recorre la ruta configurada en segundo plano. Si existe una estructura por fecha, consulta directamente las carpetas de año, mes y día relacionadas con la base activa.

#### Issabel mediante SFTP

Configura dirección, usuario, contraseña, ruta remota y huella SSH. La ruta recomendada es:

```text
/var/spool/asterisk/monitor/
```

La búsqueda filtra por teléfono y fecha, descarga solo las coincidencias y conserva una copia local. Los archivos originales del servidor no se modifican ni eliminan. La contraseña se entrega a WinSCP mediante una variable de entorno temporal y no se registra en los argumentos del proceso.

### Bases de clientes

La herramienta **Bases** permite cargar archivos CSV o Excel, consolidarlos, eliminar teléfonos duplicados y generar el formato de trabajo de Sentry. Después de procesar una base, **Usar base para buscar audios** toma los teléfonos y fechas de `Hoja1`.

Para bases de Issabel se relacionan archivos `q-<cola>-<teléfono>-<AAAAMMDD>-...` por teléfono y fecha. Para bases Lucid se acepta `export_base_*.csv`, se conservan `Celular`, `Nombre`, `ID` y `Estado`, y se buscan archivos `out-<teléfono>-<extensión>-<AAAAMMDD>-...` desde el 1 de septiembre de 2026.

También puedes usar el conversor independiente:

```powershell
python scripts\transformar_base.py archivo.xlsx
python scripts\transformar_base.py --sistema lucid export_base.csv
```

Consulta las reglas específicas en [scripts/README.md](scripts/README.md).

## Análisis y consumo de API

La pantalla **Costes API** registra cada solicitud de transcripción y análisis realizada desde esta versión. Permite filtrar por día, semana, mes o **Todo el historial**, así como por servicio y proveedor; muestra el total, el gasto de transcripción (con las horas de audio) y de análisis (con los tokens), el promedio por llamada y el ritmo mensual. La sección **Costo por API** compara cada modelo con su participación en el gasto, su consumo, su costo efectivo (USD por hora de audio en transcripción y por solicitud en análisis) y su tarifa base. El gráfico apila el gasto de cada modelo por día (por semana o por mes en períodos largos) y el detalle lista el costo por llamada y las 500 solicitudes más recientes. El botón verde **Exportar Excel**, junto a **Actualizar**, genera un libro con resumen, gastos diarios, todas las solicitudes del filtro y tarifas de referencia y aplicadas. El total suma todos los importes estimables del período seleccionado. Las respuestas reutilizadas desde caché no vuelven a sumarse. El historial anterior a esta versión no contiene datos de consumo y no se reconstruye a partir de las llamadas.

Sentry calcula los importes automáticamente a partir de la duración de audio o los tokens devueltos por la API. Usa referencias de pago por uso de [Deepgram](https://deepgram.com/pricing), [Gemini](https://ai.google.dev/gemini-api/docs/pricing), [DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/) y [Groq](https://console.groq.com/docs/models), consultadas el 23 de septiembre de 2026, y de [AssemblyAI](https://www.assemblyai.com/pricing), verificado el 16 de octubre de 2026. Para Deepgram suma Keyterm Prompting cuando envía términos sensibles. En AssemblyAI la tarifa base ya incluye la diarización estándar: 0,17 USD/h Universal-2 y 0,23 USD/h Universal-3.5 Pro; Keyterms está incluido en Universal-2 y añade 0,05 USD/h en Universal-3.5 Pro. Para DeepSeek aplica la franja pico o fuera de pico según la hora UTC de la solicitud. Cada solicitud conserva la tarifa usada para su estimación, incluidas las tarifas históricas guardadas antes de este cambio. Las tarifas personalizadas antiguas no se aplican a solicitudes nuevas. Cuando falta la tarifa o el dato de uso necesario, la solicitud aparece como **Sin estimación** y el total excluye ese importe. El costo efectivo por hora solo utiliza el audio con un costo estimable. Si el proveedor no devuelve la duración del WAV recortado, se usa la del tramo enviado. Los valores pueden diferir de la factura por planes, créditos, impuestos y cambios de tarifa; en el plan gratuito de Groq el cargo real puede ser cero aunque se muestre la tarifa de referencia.

Sentry procesa varios audios en paralelo, reutiliza conexiones HTTP y agrupa archivos con la misma huella SHA-256. Las cachés separadas de transcripción y análisis evitan repetir solicitudes cuando coinciden el audio, proveedor y modelo; cambiar los términos reutiliza la transcripción y vuelve a evaluar el contexto. Para reducir el gasto de transcripción, en WAV compatibles (PCM, mu-law o A-law) solo se recorta el silencio digital exacto del inicio y del final. No se descarta una señal por su volumen o duración; las marcas de tiempo se corrigen para coincidir con el audio original. Los WAV compuestos únicamente por silencio digital no se envían a la API. Los formatos no compatibles se envían sin cambios. Cambiar de proveedor o modelo permite obtener una transcripción distinta y puede generar un nuevo cargo. Si la conexión se corta después de enviar un audio o el servidor devuelve un error ambiguo, no se reenvía automáticamente para reducir el riesgo de cobro doble. Reintentar manualmente puede generar otro cargo.

Si no hay términos sensibles, la clasificación normal se realiza localmente y no consume la API contextual. Cuando existen candidatos, Gemini o DeepSeek reciben únicamente fragmentos cercanos a las coincidencias.

Al agregar o modificar términos en **Configuración**, las llamadas ya analizadas se vuelven a evaluar automáticamente al terminar la edición o tras una breve pausa. Si hay un análisis o una búsqueda en curso, la reevaluación espera y utiliza los últimos términos configurados. Se reutilizan las transcripciones si el audio, proveedor y modelo no cambian; la nueva validación contextual puede consumir API. Las llamadas pendientes de su primer análisis siguen iniciándose con **Analizar**.

Las llamadas normales y los buzones se reclasifican como denuncias cuando la validación contextual confirma riesgo relacionado con los términos. Una grabación breve o con un solo hablante no se descarta antes de revisar las coincidencias.

**Marcar como verificada** está disponible para denuncias, llamadas normales y buzones ya analizados. Al marcarla, la llamada pasa a Denuncias y se incluye en filtros, métricas, reportes y exportaciones de verificadas. La decisión manual persiste al reanalizar, incluso si falla un servicio. **Quitar verificación** restaura la última clasificación automática, sin borrar el análisis ni inventar evidencias.

La transcripción acompaña la reproducción. Cuando el proveedor entrega marcas por palabra, el texto avanza con esos tiempos; los resultados anteriores utilizan una interpolación local.

## Datos y almacenamiento

Durante el desarrollo, SQLite se guarda en:

```text
data/db/sentry_audit.db
```

En una instalación de Windows se guarda en:

```text
%LOCALAPPDATA%\Ecuaconexion\Sentry\data\db\sentry_audit.db
```

La base contiene llamadas, términos detectados, transcripciones, análisis, ajustes, trabajos de transformación y relaciones con bases de clientes. SQLite no está cifrado; la protección del equipo y los permisos del sistema de archivos son necesarios.

No subas al repositorio:

- bases SQLite;
- grabaciones de audio;
- reportes o archivos Excel generados;
- claves, contraseñas o variables de entorno.

Para preparar el esquema sin abrir la interfaz:

```powershell
python -m app.database
```

## Construcción del instalador

Instala las dependencias de construcción y Inno Setup 6:

```powershell
python -m pip install -r requirements-build.txt
winget install --id JRSoftware.InnoSetup --exact
```

Construye el instalador con:

```powershell
.\Construir_EXE.cmd
```

El resultado se genera en `dist\Sentry_Setup_<versión>.exe`. La compilación crea también `dist\Sentry_Setup.exe`, una copia para el botón de descarga del README.

Para publicar una versión:

1. Actualiza `APP_VERSION` en `app/about.py` y ejecuta las pruebas.
2. Crea un Release con la etiqueta `v<versión>` apuntando al commit correspondiente de `main`.
3. Adjunta `Sentry_Setup_<versión>.exe`, `Sentry_Setup.exe`, `sentry-update.json` y `SHA256SUMS`. El archivo de nombre fijo permite descargar directamente el instalador del último Release; el actualizador utiliza el archivo con versión.
4. Valida la actualización en una instalación limpia antes de distribuirla.

La verificación SHA-256 comprueba integridad, pero no sustituye una firma digital de código.

## Pruebas

Ejecuta la suite completa con:

```powershell
python -m unittest discover -s tests -v
```

Validaciones adicionales:

```powershell
python -m ruff check app scripts tests --select F
python -m compileall -q app scripts
```

Las pruebas utilizan archivos y bases temporales y no deben modificar la información local del usuario.

## Estructura

| Ruta | Contenido |
| --- | --- |
| `app/` | Aplicación, interfaz, persistencia y servicios |
| `app/ui/assets/` | Logos, iconos, fuentes y recursos visuales |
| `scripts/` | Conversión de bases y construcción del instalador |
| `tests/` | Pruebas unitarias y de integración |
| `Sentry.spec` | Configuración de PyInstaller |
| `SentryInstaller.iss` | Configuración de Inno Setup |
| `ARQUITECTURA.md` | Diseño técnico y flujo de procesamiento |
| `SPECS.md` | Reglas funcionales y de producto |
| `LICENSE` | Licencia propietaria y condiciones de uso |

## Seguridad y confidencialidad

Sentry es software de uso interno exclusivo de Ecuaconexión. Configura las credenciales en cada equipo, limita el acceso a la base y a las carpetas de salida y valida los proveedores antes de procesar grabaciones reales.

Para reportar un problema o proponer una mejora, abre un [Issue](https://github.com/jacksonandresrosales/Sentry/issues) sin incluir claves, grabaciones, teléfonos completos ni transcripciones de clientes.

Software desarrollado para uso interno de Ecuaconexión.

## Licencia y propiedad

Sentry es software propietario desarrollado por **Jackson Ocaña** y **Jeremy Godoy** para **Ecuaconexión**.

Queda prohibido utilizar, copiar, modificar, distribuir, sublicenciar o comercializar este software, total o parcialmente, fuera de Ecuaconexión sin autorización previa y por escrito de sus titulares. La publicación del código en GitHub no concede derechos de uso externo ni lo convierte en software de código abierto.

Consulta todos los términos en [`LICENSE`](LICENSE).
