# Transformar bases de Issabel y Lucid

## Sistema de origen

Seleccione **Issabel** o **Lucid** antes de cargar la base, tanto en Sentry como en la mini aplicación. Issabel es la opción predeterminada y conserva las reglas del formato NO descritas abajo.

Para Lucid, el formato principal es la exportación `export_base_*.csv`. El conversor lee sus columnas `Celular`, `Nombre`, `ID` y `Estado` y genera una sola hoja `Hoja1` con **Teléfono, Nombre, ID y Estado**. `Sub-Estado` es una columna distinta y no reemplaza ni complementa `Estado`, incluso si este último está vacío. Se mantiene la compatibilidad con el consolidado anterior, cuya columna de estado se llama `GESTION`.

Conserva todos los estados de los registros seleccionados, normaliza el teléfono al formato nacional con un solo cero inicial y retira duplicados conservando la primera aparición. Mantiene el ID como texto y conserva sus ceros iniciales; si el origen no tiene ID, la columna queda vacía. No añade planes, notas, fechas, columnas auxiliares ni copias de las hojas originales. No filtra por `ELIMINAR DE LA BASE` ni aplica los filtros de Issabel a Lucid.

Por defecto, Lucid conserva personas y excluye empresas identificadas por una forma societaria explícita en el nombre, como `S.A.`, `S.A.S.`, `Ltda.` o `S.C.C.`. La regla se basa en la razón social, no en tener un RUC de 13 dígitos: una persona con RUC permanece incluida. Si el nombre no permite identificar una empresa de forma concluyente, se conserva. El filtro se aplica antes de retirar duplicados y el resultado informa cuántos registros fueron excluidos; el archivo original no cambia.

```powershell
python scripts/transformar_base.py --sistema lucid "C:\Bases\export_base_campana.csv"
```

La interfaz de Sentry y la mini aplicación utilizan el filtro de personas. Desde la línea de comandos puede seleccionarse explícitamente otro alcance:

```powershell
python scripts/transformar_base.py --sistema lucid --entidades people "C:\Bases\export_base_campana.csv"
python scripts/transformar_base.py --sistema lucid --entidades all "C:\Bases\export_base_campana.csv"
python scripts/transformar_base.py --sistema lucid --entidades companies "C:\Bases\export_base_campana.csv"
```

`people` es el valor predeterminado; `all` incluye todos los registros y `companies` conserva únicamente las empresas identificadas por la regla anterior. No se realiza una validación legal del tipo de documento o de la entidad.

La salida se llama `<nombre_original>_Lucid.xlsx`; las repeticiones generan `_Lucid_2.xlsx`, `_Lucid_3.xlsx`, etc. Los teléfonos se escriben como texto para conservar el cero. Los prefijos ecuatorianos `593`, `+593` y `00593` se convierten al formato nacional. Una fila con un teléfono vacío o inválido produce un mensaje con el registro que debe corregirse.

Las opciones de estado de llamada, estado del formulario, RUC, tipo y número de base se aplican solamente a Issabel. Lucid no las necesita. Las opciones de hoja Excel, separador y codificación CSV funcionan para ambos sistemas.

En Sentry, las bases de Lucid se relacionan por teléfono exclusivamente con grabaciones `out-` desde el 1 de septiembre de 2026 (`20260901`), inclusive, en el origen de audio configurado. La fecha se toma del nombre de la grabación, no del nombre del CSV ni de `Fecha Rellamada`, que no representa la fecha de una grabación. Los archivos `q-` y los audios anteriores quedan excluidos de Lucid; las reglas de Issabel no cambian. La búsqueda remota muestra el progreso y los resultados encontrados de forma incremental; **Detener búsqueda** interrumpe el recorrido conservando las coincidencias disponibles. La exportación de auditoría conserva las cuatro columnas y actualiza el estado de las denuncias detectadas o verificadas. Las bases Lucid de tres columnas de versiones anteriores siguen siendo compatibles y se exportan con el ID vacío.

## Mini aplicación

También puedes usar esta funcionalidad dentro de Sentry: abre `Sentry.cmd` en la carpeta principal y pulsa **Bases** junto al directorio del header. Esa vista permite seleccionar varios archivos, consultar el historial persistente y usar las opciones avanzadas del mismo motor. La mini aplicación independiente se mantiene disponible como antes.

Haz doble clic en `DB_delete.cmd`, en la carpeta principal del proyecto. Se abre una ventana para:

1. Cargar tu base CSV o Excel desde cualquier carpeta.
2. Elegir la carpeta de resultados (por defecto `outputs`).
3. Pulsar **Procesar y guardar Excel**.
4. Abrir el resultado o su carpeta con los botones de la ventana.

El nombre incluye un indicador de base: `<nombre_original>_DB_delete_B1.xlsx`. Si no hay un número en el origen, cargas sucesivas con el mismo nombre generan B1, B2, B3... en la carpeta elegida. Si el origen ya indica B5, conserva ese número y distingue repeticiones con versiones: `_DB_delete_B5.xlsx`, `_DB_delete_B5_2.xlsx`, `_DB_delete_B5_3.xlsx`. La aplicación nunca reemplaza resultados anteriores ni modifica la base original; muestra el nombre definitivo y permite abrir su carpeta.

También puedes abrirla desde PowerShell:

```powershell
.\DB_delete.cmd
```

## Uso por comandos

En este equipo Windows puedes ejecutar el lanzador desde la carpeta del proyecto. Utiliza Python y las dependencias incluidas con Codex si están disponibles:

```powershell
.\scripts\transformar_base.cmd "C:\Bases\CAMP26 MC JUL S2 B1.csv"
```

Sin argumentos abre la mini aplicación:

```powershell
.\scripts\transformar_base.cmd
```

En otro equipo con Python instalado, instala las dependencias una vez:

```powershell
python -m pip install -r scripts/requirements.txt
```

Convierte una base:

```powershell
python scripts/transformar_base.py "C:\Bases\CAMP26 MC JUL S2 B1.csv"
```

Sin argumentos abre una ventana para seleccionar uno o varios CSV/Excel:

```powershell
python scripts/transformar_base.py
```

El Excel se guarda en `outputs/<nombre_original>_DB_delete_B<n>.xlsx`, con numeración y versiones automáticas para evitar sobrescrituras, incluso ante guardados simultáneos. Si indicas un destino explícito con `-o`, se rechaza si ya existe; solo `-o` junto con `--sobrescribir` permite reemplazarlo deliberadamente. El guardado utiliza un archivo temporal y no publica resultados incompletos.

## Reglas del modelo

Las siguientes reglas corresponden exclusivamente a Issabel; no sustituyen el filtro de empresas de Lucid descrito anteriormente.

- Detecta la cabecera real aunque antes haya filas como `FORMULARIO` o `Column1...`.
- Acepta encabezados con acentos o los caracteres dañados que aparecen en el CSV de referencia.
- Por defecto selecciona `ESTADO = ELIMINAR DE BASE DE DATOS` y `Estado Llamada = Success`.
- Excluye IDs numéricos de 13 dígitos cuyo tercer dígito es `9`. Esta regla se dedujo al comparar las ocho bases originales con sus hojas filtradas: reproduce los mismos 192 registros, en el mismo orden. Se puede desactivar con `--incluir-ruc-tercer-digito-9`.
- Ordena las columnas: Teléfono, Estado Llamada, Agente, Fecha y hora, Duración(Seg), Nombre, ID, TIPO ID, ESTADO, T BASE, N° BASE.
- Reduce los espacios repetidos dentro de los nombres en los resultados, conservando el texto original en la hoja de la base.
- Elimina bloques de dos o más apellidos repetidos al final si existen al menos dos palabras anteriores, como la corrección encontrada en NO. No elimina apellidos individuales iguales, como GARCIA GARCIA.
- Elimina puntos aislados al final del nombre, sin quitar el punto de una inicial como A.
- Escribe una fórmula real de Excel en TIPO ID: 10 caracteres → CÉDULA; 13 → RUC; otra longitud → vacío. Incluye el resultado calculado para lectores que no recalculan fórmulas. Esta regla clasifica por longitud, no valida legalmente el documento.
- Mantiene teléfonos, IDs y agentes como texto, incluidos sus ceros iniciales. En Excel recupera ceros si la celda tiene un formato explícito como `0000000000`; los ceros ya perdidos en una celda numérica sin ese formato no se pueden reconstruir.
- `T BASE`: V significa verificar y R significa remover. Conserva la columna de origen o utiliza `--tipo-base V` / `--tipo-base R`. Si falta, asigna V para dejar el registro pendiente de revisión. No cambia por su cuenta un V a R: hace falta una marca del origen o una decisión de revisión. Marcar R no borra datos del origen.
- `N° BASE` conserva la columna de origen o detecta B1, B2... en el nombre; si falta, asigna un número automáticamente. También admite `--numero-base B1` para una sola base. Acepta `1` o `b001` como B1 y rechaza números inválidos, cero y valores de más de nueve dígitos.
- Las hojas originales conservan la estructura `Column1...`, la fila de FORMULARIO y la cabecera real dentro de una tabla de Excel `TableStyleMedium7`, con filtros desplegables, como NO.
- Intercala cada original con su resultado filtrado: `BASE1`, `BASE2`, `Hoja3`, `Hoja4`... según el número de base. Las hojas filtradas tienen botones AutoFilter, fondo verde y resaltado rojo en A:I cuando `T BASE = R`. El resaltado cambia al editar esa columna.
- `Validar` reúne los resultados de todas las bases y tiene filtros hasta N° BASE.
- `Hoja1` conserva las fórmulas dinámicas reales `UNIQUE` + `VLOOKUP` de NO, con resultados precalculados. Conserva la primera aparición de cada teléfono, incluidos vacíos repetidos. Los cambios dentro del rango generado de `Validar` se reflejan al recalcular en Excel; agregar nuevas filas fuera del rango requiere regenerar el archivo. Estas fórmulas requieren Excel con soporte de matrices dinámicas, igual que la referencia.
- Al leer un Excel con varias bases originales, evita importar otra vez sus hojas de resultados. Para escoger una hoja específica, utiliza `--hoja`.

NO no guarda criterios activos en sus tablas: conserva los botones de filtro y las hojas de resultados ya seleccionados. El script reproduce esa estructura con rangos actualizados, sin copiar referencias antiguas a miles de filas vacías. No hace falta pasar NO.xlsx cada vez. La referencia no conserva una fórmula para asignar V/R; el script utiliza la columna de origen o el valor indicado por el usuario.

La hoja final consulta `Validar`, como en el modelo. Si corriges manualmente T BASE después de generar el Excel, hazlo en `Validar` para que `Hoja1` recoja el cambio.

## Opciones

Indicar tipo y número de base y destino:

```powershell
python scripts/transformar_base.py "base.csv" --tipo-base V --numero-base B1 -o "outputs/resultado.xlsx"
```

Consolidar varias bases:

```powershell
python scripts/transformar_base.py "campana B1.csv" "campana B2.csv" -o "outputs/consolidado_NO.xlsx"
```

Incluir también ShortCall u otros estados de llamada, manteniendo el filtro ELIMINAR:

```powershell
python scripts/transformar_base.py "base.csv" --estado-llamada "*"
```

Transformar todos los registros sin filtrar:

```powershell
python scripts/transformar_base.py "base.csv" --estado "*" --estado-llamada "*" --incluir-ruc-tercer-digito-9
```

Seleccionar una hoja o especificar separador/codificación:

```powershell
python scripts/transformar_base.py "base.xlsx" --hoja "Datos"
python scripts/transformar_base.py "base.csv" --separador ";" --codificacion cp1252
```

Ver todas las opciones y ejecutar las pruebas:

```powershell
python scripts/transformar_base.py --help
python -m unittest discover -s tests -p "test_transformar_base.py" -v
```
