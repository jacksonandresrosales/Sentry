# Sentry

Sentry es una aplicación de escritorio para Windows orientada a la auditoría de llamadas. Centraliza la búsqueda de grabaciones, la transcripción, el análisis contextual, la revisión de evidencias y la generación de reportes.

La aplicación relaciona grabaciones con bases de clientes mediante teléfono y fecha, identifica palabras o frases sensibles y clasifica cada llamada como alerta, buzón o llamada normal.

Versión actual: `0.1.0-beta.1`

## Funcionalidades principales

- Búsqueda de grabaciones en carpetas locales, unidades NAS e Issabel mediante SFTP.
- Asociación de audios con clientes por número telefónico y fecha.
- Transcripción mediante Deepgram u OpenAI.
- Análisis contextual mediante Google Gemini u OpenAI.
- Clasificación de llamadas en alertas, buzones y llamadas normales.
- Identificación visual de términos sensibles y fragmentos de evidencia.
- Reproducción sincronizada con la transcripción.
- Verificación manual de denuncias detectadas.
- Transformación y consolidación de bases CSV y Excel.
- Historial persistente de llamadas, bases procesadas y resultados.
- Reportes por día, semana o mes con exportación a Excel.
- Temas claro y oscuro.

## Requisitos del sistema

### Aplicación instalada

- Windows 10 u 11 de 64 bits.
- Acceso a Internet para los servicios de transcripción y análisis.
- Credenciales válidas para los proveedores de inteligencia artificial seleccionados.
- Acceso de red al NAS o servidor Issabel cuando corresponda.

El instalador incluye Python, Qt, WinSCP y las dependencias necesarias para ejecutar la aplicación.

### Entorno de desarrollo

- Python 3.10 o superior.
- Dependencias incluidas en `requirements.txt`.
- Inno Setup 6 para construir el instalador de Windows.

## Instalación recomendada

Descarga el instalador más reciente desde la sección [Releases](https://github.com/jacksonandresrosales/Sentry/releases) del repositorio y ejecútalo.

El instalador:

- instala Sentry en la ubicación seleccionada;
- crea accesos directos en el menú Inicio y, opcionalmente, en el escritorio;
- incorpora una base de datos inicial vacía;
- conserva los datos del usuario fuera de la carpeta protegida del programa.

Cada equipo debe configurar sus propias claves API y credenciales de conexión desde la pantalla **Configuración**.

## Ejecución desde el código fuente

En Windows, puede utilizarse el instalador de desarrollo incluido:

```powershell
.\Instalar_Sentry.cmd
```

Este comando crea un entorno virtual, instala las dependencias e inicializa el esquema local. Después puede iniciarse la aplicación con `Sentry.vbs`, que evita mostrar una ventana de consola.

La instalación manual equivalente es:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app.main
```

## Configuración de servicios

Sentry permite seleccionar de forma independiente el proveedor de transcripción y el proveedor de análisis contextual.

Las credenciales pueden introducirse desde la interfaz o suministrarse mediante variables de entorno:

| Servicio | Variables admitidas |
| --- | --- |
| Deepgram | `DEEPGRAM_API_KEY` |
| Google Gemini | `GEMINI_API_KEY`, `GOOGLE_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |

También pueden definirse `SENTRY_TRANSCRIPTION_PROVIDER`, `SENTRY_TRANSCRIPTION_MODEL`, `SENTRY_ANALYSIS_PROVIDER` y `SENTRY_ANALYSIS_MODEL` para seleccionar proveedores y modelos al iniciar.

Las claves guardadas desde la interfaz se protegen mediante DPAPI y sólo pueden descifrarse con el mismo usuario de Windows.

## Fuentes de grabaciones

### Carpeta local o NAS

Sentry recorre la ruta configurada en segundo plano. Cuando detecta una estructura por fecha, accede directamente a las carpetas de año, mes y día requeridas por la base activa.

### Issabel mediante SFTP

La configuración solicita dirección del servidor, usuario, contraseña, ruta remota y huella SSH. La ruta recomendada es:

```text
/var/spool/asterisk/monitor/
```

La búsqueda limita el recorrido a las fechas y teléfonos de la base activa. Sólo descarga las coincidencias y conserva una copia local para búsquedas posteriores. Los archivos originales del servidor no se modifican ni eliminan.

La contraseña SFTP se entrega a WinSCP mediante una variable de entorno temporal; no se incluye en los argumentos del proceso ni en los registros de Sentry.

## Bases de clientes

La herramienta **Bases** permite cargar uno o varios archivos CSV o Excel, consolidar registros, eliminar teléfonos duplicados y generar el formato de trabajo utilizado por Sentry.

También puede utilizarse el conversor independiente:

```powershell
python scripts\transformar_base.py archivo.xlsx
```

La documentación específica del conversor se encuentra en [scripts/README.md](scripts/README.md).

Al seleccionar **Usar base para buscar audios**, Sentry toma el teléfono y la fecha de `Hoja1`. Los archivos con nombres compatibles, como `q-<cola>-<teléfono>-<AAAAMMDD>-...`, se incorporan únicamente cuando ambos datos coinciden.

## Análisis y reutilización de resultados

Sentry procesa hasta tres audios en paralelo y guarda cada resultado al finalizar. Las cachés de transcripción y análisis se administran por separado para reducir tiempo y consumo de API.

Cuando el usuario agrega o modifica palabras y frases clave, las llamadas completadas vuelven a evaluarse con la configuración actual. La transcripción existente se reutiliza, por lo que no se solicita nuevamente al proveedor si el audio no cambió.

La aplicación también conserva una huella SHA-256, el tamaño y la fecha de modificación de cada archivo. Si el contenido no cambió, evita volver a leer y transcribir el audio innecesariamente.

## Datos y almacenamiento

Durante el desarrollo, la base SQLite se guarda en:

```text
data/db/sentry_audit.db
```

En una instalación de Windows se guarda en:

```text
%LOCALAPPDATA%\Ecuaconexion\Sentry\data\db\sentry_audit.db
```

La base registra llamadas, términos detectados, transcripciones, análisis, configuraciones, trabajos de transformación y relaciones con bases de clientes.

Los siguientes elementos se excluyen del repositorio porque pueden contener información sensible:

- bases SQLite locales;
- archivos de audio;
- reportes y archivos Excel generados;
- variables de entorno y credenciales.

Para preparar el esquema sin abrir la interfaz:

```powershell
python -m app.database
```

Para realizar un respaldo, cierre Sentry y copie la base SQLite y los reportes que desee conservar. SQLite no está cifrado; la protección del equipo y los permisos del sistema de archivos continúan siendo necesarios.

## Construcción del instalador

Instale las dependencias de construcción:

```powershell
python -m pip install -r requirements-build.txt
winget install --id JRSoftware.InnoSetup --exact
```

Después ejecute:

```powershell
.\Construir_EXE.cmd
```

El resultado se genera en:

```text
dist\Sentry_Setup_<versión>.exe
```

La versión, el nombre del instalador y sus metadatos se obtienen de `app/about.py`. El proceso de construcción crea una base nueva con el esquema vigente y comprueba que no contenga llamadas, configuraciones ni credenciales del equipo de desarrollo.

## Pruebas y validación

Ejecute la suite completa con:

```powershell
python -m unittest discover -s tests -v
```

Validación estática utilizada por el proyecto:

```powershell
python -m ruff check app scripts tests --select F
python -m compileall -q app scripts
```

Las pruebas trabajan con archivos y bases temporales; no deben modificar la base local del usuario.

## Estructura del proyecto

| Ruta | Contenido |
| --- | --- |
| `app/` | Aplicación, interfaz, persistencia y servicios |
| `app/ui/assets/` | Iconos, fuentes y recursos visuales |
| `scripts/` | Conversión de bases y construcción del instalador |
| `tests/` | Pruebas unitarias y de integración |
| `Sentry.spec` | Configuración de PyInstaller |
| `SentryInstaller.iss` | Configuración de Inno Setup |
| `ARQUITECTURA.md` | Diseño técnico y flujo de procesamiento |

## Seguridad y confidencialidad

Sentry está diseñado para uso interno y puede procesar grabaciones, teléfonos, identificadores y transcripciones. El acceso al equipo, a la base de datos y a las carpetas de salida debe limitarse al personal autorizado.

Las credenciales no deben añadirse al repositorio ni incluirse en archivos distribuidos. Antes de publicar una versión deben ejecutarse las pruebas, comprobarse que la base incorporada esté vacía y verificarse el instalador en una instalación limpia.

## Estado del proyecto

La versión actual es beta. Se recomienda validar el instalador y los proveedores configurados en un entorno controlado antes de utilizarlo en producción.

Software desarrollado para uso interno de Ecuaconexión.
