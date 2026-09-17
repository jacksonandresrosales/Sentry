<p align="center">
  <img src="app/ui/assets/sentry-logo.png" alt="Logo de Sentry" width="280">
</p>

<h1 align="center">Sentry</h1>

<p align="center">
  Auditoría inteligente de llamadas para equipos que necesitan encontrar, entender y verificar evidencias de atención al cliente.
</p>

<p align="center">
  <a href="https://github.com/jacksonandresrosales/Sentry/releases"><img src="https://img.shields.io/github/v/release/jacksonandresrosales/Sentry?include_prereleases&label=release&color=27953c" alt="Última release"></a>
  <a href="https://github.com/jacksonandresrosales/Sentry/releases"><img src="https://img.shields.io/badge/estado-beta-f0b429" alt="Estado beta"></a>
  <img src="https://img.shields.io/badge/Windows-10%2F11-0078D6?logo=windows&logoColor=white" alt="Windows 10 y 11">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10 o superior">
  <img src="https://img.shields.io/badge/Qt-PySide6-41CD52?logo=qt&logoColor=white" alt="PySide6">
  <img src="https://img.shields.io/badge/base%20local-SQLite-003B57?logo=sqlite&logoColor=white" alt="SQLite">
</p>

<p align="center">
  <a href="https://github.com/jacksonandresrosales/Sentry/releases">Descargar</a> ·
  <a href="ARQUITECTURA.md">Arquitectura</a> ·
  <a href="SPECS.md">Especificaciones</a> ·
  <a href="https://github.com/jacksonandresrosales/Sentry/issues">Reportar un problema</a>
</p>

---

## Qué es Sentry

Sentry es una aplicación de escritorio para Windows que centraliza la auditoría de grabaciones telefónicas. Busca audios en carpetas locales, unidades NAS o Issabel, los relaciona con bases de clientes y permite revisar cada caso con transcripción, análisis contextual, reproducción sincronizada y reportes.

La aplicación está orientada al uso interno de Ecuaconexión. Puede procesar información sensible, por lo que las credenciales, bases, grabaciones y reportes deben mantenerse en equipos y carpetas con acceso restringido.

> **Versión actual:** `0.1.0-beta.8` · La versión beta debe validarse en un entorno controlado antes de utilizarse en producción.

## Funcionalidades

- Búsqueda en carpetas locales, NAS e Issabel mediante SFTP.
- Asociación de llamadas con clientes por teléfono y fecha.
- Transcripción con Deepgram u OpenAI.
- Análisis contextual con Google Gemini u OpenAI.
- Clasificación en alertas, buzones y llamadas normales.
- Detección y etiquetado de palabras o frases sensibles.
- Reproducción de audio sincronizada con la transcripción.
- Verificación manual de denuncias detectadas.
- Transformación y consolidación de bases CSV y Excel.
- Historial persistente y reportes por día, semana o mes.
- Exportación de resultados a Excel.
- Temas claro y oscuro independientes de Windows.
- Actualizaciones verificadas desde la propia aplicación.

## Instalación recomendada

Descarga el instalador más reciente desde [Releases](https://github.com/jacksonandresrosales/Sentry/releases) y ejecútalo. El instalador incluye Python, Qt, WinSCP y las dependencias necesarias.

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
| Deepgram | `DEEPGRAM_API_KEY` |
| Google Gemini | `GEMINI_API_KEY`, `GOOGLE_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |

También puedes definir `SENTRY_TRANSCRIPTION_PROVIDER`, `SENTRY_TRANSCRIPTION_MODEL`, `SENTRY_ANALYSIS_PROVIDER` y `SENTRY_ANALYSIS_MODEL`.

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

Sentry procesa varios audios en paralelo, reutiliza conexiones HTTP y agrupa archivos con la misma huella SHA-256. Las cachés separadas de transcripción y análisis evitan repetir solicitudes cuando coinciden el audio, el modelo y los términos configurados.

Si no hay términos sensibles, la clasificación normal se realiza localmente y no consume la API contextual. Cuando existen candidatos, Gemini u OpenAI reciben únicamente fragmentos cercanos a las coincidencias.

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

El resultado se genera en `dist\Sentry_Setup_<versión>.exe`.

Para publicar una versión:

1. Actualiza `APP_VERSION` en `app/about.py` y ejecuta las pruebas.
2. Crea un Release con la etiqueta `v<versión>` apuntando al commit correspondiente de `main`.
3. Adjunta el instalador, `sentry-update.json` y `SHA256SUMS`.
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
