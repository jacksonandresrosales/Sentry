# Sentry

Sentry es una aplicación de escritorio para Windows orientada a la auditoría de llamadas. Centraliza la búsqueda de grabaciones, la transcripción, el análisis contextual, la revisión de evidencias y la generación de reportes.

La aplicación relaciona grabaciones con bases de clientes mediante teléfono y fecha, identifica palabras o frases sensibles y clasifica cada llamada como alerta, buzón o llamada normal.

Versión actual: `0.1.0-beta.6`

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
- Actualizaciones verificadas desde la aplicación, con respaldo previo y conservación de datos.
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

Descargue el instalador más reciente desde [Releases de Sentry](https://github.com/jacksonandresrosales/Sentry/releases) y ejecútelo. El repositorio original reúne el código fuente en `main` y los instaladores en Releases.

El instalador:

- instala Sentry en la ubicación seleccionada;
- crea accesos directos en el menú Inicio y, opcionalmente, en el escritorio;
- incorpora una base de datos inicial vacía;
- conserva los datos del usuario fuera de la carpeta protegida del programa.

Cada equipo debe configurar sus propias claves API y credenciales de conexión desde la pantalla **Configuración**.

## Actualizaciones de la aplicación

La versión `0.1.0-beta.6` se instala manualmente una vez en los equipos con versiones anteriores, incluida la beta.5 distribuida desde otro repositorio. A partir de esta versión, Sentry consulta y descarga las siguientes publicaciones del repositorio original `jacksonandresrosales/Sentry` desde **Configuración → Actualizaciones de Sentry**, sin solicitar una cuenta de GitHub mientras el repositorio permanezca público.

- La aplicación instalada consulta las versiones al iniciar cuando corresponde y cada seis horas. La comprobación automática puede desactivarse; el botón **Buscar actualizaciones** permite consultar manualmente.
- **Descargar actualización** obtiene el instalador del repositorio público configurado y verifica su versión, tamaño y huella SHA-256 antes de habilitar la instalación.
- **Instalar y reiniciar** requiere confirmación. Deben finalizarse o detenerse los análisis, las búsquedas y las exportaciones antes de continuar.
- Antes de ejecutar el instalador se guardan los ajustes y se crea y comprueba un respaldo de SQLite. Si falla la descarga, la verificación o el respaldo, la actualización no se instala.
- La actualización reemplaza los archivos del programa, no los datos persistentes de cada cliente en `%LOCALAPPDATA%\Ecuaconexion\Sentry`. Se conservan las bases, el historial, los resultados, las grabaciones descargadas y la configuración del mismo usuario de Windows.

Los respaldos previos a la actualización se guardan en `%LOCALAPPDATA%\Ecuaconexion\Sentry\backups`. Las versiones estables no reciben versiones beta automáticamente; las instalaciones beta pueden recibir nuevas betas y versiones estables posteriores.

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

La búsqueda usa las reglas del sistema de origen de la base activa. Issabel compara teléfono y fecha de llamada; Lucid busca únicamente grabaciones `out-` desde el 1 de septiembre de 2026 y compara el teléfono normalizado. Sólo descarga las coincidencias y conserva una copia local para búsquedas posteriores. Los archivos originales del servidor no se modifican ni eliminan.

Durante la búsqueda se muestra el progreso y las grabaciones encontradas aparecen de forma incremental, sin esperar a que termine todo el recorrido. El botón **Detener búsqueda** permite interrumpirla y conservar los resultados ya disponibles.

El filtrado de nombres y fechas se realiza mediante un componente .NET compilado, y las carpetas fechadas recientes se revisan primero. Cada búsqueda vuelve a consultar los listados remotos para detectar grabaciones nuevas. Las copias locales se reutilizan únicamente cuando coinciden el tamaño y la fecha de modificación; no se utiliza una lista remota antigua para omitir resultados. El tiempo total también depende del servidor, la red y el volumen de grabaciones.

La contraseña SFTP se entrega a WinSCP mediante una variable de entorno temporal; no se incluye en los argumentos del proceso ni en los registros de Sentry.

## Bases de clientes

La herramienta **Bases** permite cargar uno o varios archivos CSV o Excel, consolidar registros, eliminar teléfonos duplicados y generar el formato de trabajo utilizado por Sentry.

Antes de cargar un archivo, seleccione **Sistema de origen**:

| Sistema | Entrada y transformación | Búsqueda de grabaciones |
| --- | --- | --- |
| Issabel | Conserva el formato NO, los filtros, las once columnas y las hojas existentes. | Por teléfono y fecha de llamada. |
| Lucid | Lee `Celular`, `Nombre`, `ID` y `Estado` del archivo `export_base_*.csv`; genera una sola hoja con `Teléfono`, `Nombre`, `ID` y `Estado`. Excluye empresas identificadas por su razón social. | Por teléfono, sólo archivos `out-` desde el 1 de septiembre de 2026, en la carpeta local, NAS o servidor SFTP configurado. |

Lucid utiliza la columna `Estado`, no `Sub-Estado`. Conserva todos los estados de los registros seleccionados y la primera aparición de cada teléfono. El ID se mantiene como texto, incluidos sus ceros iniciales; si el origen no contiene esta columna, queda vacío. Planes, notas y demás columnas no se incluyen en el Excel de salida. También acepta el formato anterior `consolidado_*.csv`, que proporciona `GESTION` en lugar de `Estado`, y las bases Lucid de tres columnas generadas por versiones anteriores.

Por defecto, la conversión conserva personas y excluye registros cuyo nombre contiene una forma societaria explícita, como `S.A.`, `S.A.S.` o `Ltda.`. No se descarta una persona por tener un RUC de 13 dígitos. Los nombres sin un indicador concluyente se conservan; esta regla no sustituye una revisión de la razón social. El filtro se aplica antes de retirar teléfonos duplicados y la interfaz informa cuántos registros de empresas excluyó. Los archivos originales no se modifican.

Los teléfonos se guardan como texto: se añade el cero nacional cuando falta y se conserva si ya existe. Los prefijos ecuatorianos `593`, `+593` y `00593` se convierten al formato nacional.

El CSV de Lucid no contiene fechas de grabación. `Fecha Rellamada` corresponde a una rellamada programada y no se usa para filtrar audios. Sentry tampoco utiliza la fecha del nombre del CSV: el inicio de búsqueda para Lucid es el 1 de septiembre de 2026 (`20260901`), inclusive, y la fecha se comprueba en el nombre de cada grabación `out-`. Los archivos `q-` y las grabaciones anteriores quedan excluidos de Lucid. El sistema de origen de la base y la ubicación de las grabaciones se configuran por separado.

Para usar la nueva base: seleccione **Lucid**, cargue el CSV, pulse **Procesar y guardar Excel** y después **Usar base para buscar audios**.

También puede utilizarse el conversor independiente:

```powershell
python scripts\transformar_base.py archivo.xlsx
```

Para Lucid:

```powershell
python scripts\transformar_base.py --sistema lucid export_base.csv
```

La documentación específica del conversor se encuentra en [scripts/README.md](scripts/README.md).

Los archivos con nombres compatibles se relacionan con `Hoja1`. Issabel utiliza `q-<cola>-<teléfono>-<AAAAMMDD>-...` y exige teléfono y fecha. Lucid utiliza `out-<teléfono>-<extensión>-<AAAAMMDD>-...`, compara el teléfono normalizado y exige una fecha igual o posterior a `20260901`.

La exportación de Lucid mantiene únicamente **Teléfono, Nombre, ID y Estado**. Una denuncia verificada recibe el estado `Denuncia verificada`; una alerta automática, `Denuncia detectada`. Los demás registros conservan su estado original. Los filtros de exportación permiten obtener denuncias automáticas, verificadas, todas las denuncias o toda la base. Issabel conserva su exportación anterior con identificación del cliente.

## Análisis y reutilización de resultados

Sentry procesa seis audios en paralelo de forma predeterminada y guarda cada resultado al finalizar. La concurrencia puede ajustarse entre una y ocho llamadas desde **Configuración** para adaptarla a la conexión y a las cuotas del proveedor. El procesamiento comienza sin esperar a calcular la huella de todo el lote; la lectura de archivos y las peticiones se mantienen acotadas al número de trabajos simultáneos.

Las cachés de transcripción y análisis se administran por separado para reducir tiempo y consumo de API. Los archivos con contenido idéntico pueden reutilizar el mismo resultado, aunque tengan nombres diferentes, sin repetir simultáneamente las mismas peticiones.

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

### Publicación de una actualización

Para distribuir una nueva versión:

1. Incremente `APP_VERSION` en `app/about.py`, ejecute las pruebas y construya el instalador.
2. Prepare un release como borrador en [Releases de Sentry](https://github.com/jacksonandresrosales/Sentry/releases) con la etiqueta `v<versión>`; por ejemplo, `v0.1.0-beta.6`, apuntando al commit correspondiente de `main`. Marque las versiones beta como prerelease.
3. Adjunte los tres archivos generados en `dist`: `Sentry_Setup_<versión>.exe`, `sentry-update.json` y `SHA256SUMS`. El manifiesto debe corresponder exactamente al instalador de ese release. Publique el borrador únicamente después de cargar los tres archivos; el actualizador no ofrece borradores.
4. Compruebe la actualización desde un equipo de prueba con una versión anterior y datos de ejemplo antes de distribuirla al resto de clientes.

El código y los instaladores se publican en el repositorio original; las bases, grabaciones y credenciales de clientes nunca deben incluirse. La verificación SHA-256 comprueba la integridad del instalador frente a la publicación; no sustituye una firma digital de código. No deben reutilizarse etiquetas o números de versión para distribuir un instalador diferente.

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
