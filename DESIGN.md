---
name: Sentry
description: Mesa de revisión clara para auditoría de llamadas
colors:
  signal-deep: "#27953c"
  action-accessible: "#1f7830"
  action-hover: "#238537"
  confirmation: "#40b73c"
  evidence: "#7eca29"
  charcoal: "#4f4c4c"
  gray: "#656263"
  gray-light: "#7a7879"
  canvas: "#f5f6f5"
  surface: "#fafbfa"
  panel: "#ffffff"
  green-soft: "#eef7ef"
  border: "#dfe3df"
  border-strong: "#c8cdc8"
  text-primary: "#202220"
  text-secondary: "#4f4c4c"
  text-muted: "#656263"
typography:
  headline:
    fontFamily: "Inter, Segoe UI, sans-serif"
    fontSize: "23px"
    fontWeight: 600
  title:
    fontFamily: "Inter, Segoe UI, sans-serif"
    fontSize: "15px"
    fontWeight: 650
  body:
    fontFamily: "Inter, Segoe UI, sans-serif"
    fontSize: "13px"
    fontWeight: 400
  label:
    fontFamily: "Inter, Segoe UI, sans-serif"
    fontSize: "10px"
    fontWeight: 550
    letterSpacing: "0.3px"
  data:
    fontFamily: "Inter, Segoe UI, sans-serif"
    fontSize: "11px"
    fontWeight: 400
rounded:
  xs: "5px"
  sm: "7px"
  md: "8px"
  lg: "10px"
spacing:
  xs: "5px"
  sm: "7px"
  md: "10px"
  lg: "14px"
  xl: "20px"
  xxl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.action-accessible}"
    textColor: "{colors.panel}"
    rounded: "{rounded.sm}"
    padding: "0 13px"
    height: "36px"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text-secondary}"
    borderColor: "{colors.border-strong}"
    rounded: "{rounded.sm}"
    padding: "0 13px"
    height: "36px"
  input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text-primary}"
    borderColor: "{colors.border-strong}"
    rounded: "{rounded.sm}"
    padding: "0 11px"
    height: "36px"
  panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.text-secondary}"
    borderColor: "{colors.border}"
    rounded: "{rounded.lg}"
---

# Design System: Sentry

## Overview

**Creative North Star: “La Mesa de Revisión Clara”**

Sentry se siente como una herramienta de escritorio serena y precisa. El blanco es el campo principal de trabajo; el gris organiza la información y el verde indica selección, evidencia o acción. La interfaz evita decorar la auditoría y deja que la llamada, el momento sensible y la transcripción construyan la jerarquía.

**Key Characteristics:**

- Superficies blancas y gris muy claro.
- Una sola familia tipográfica, Inter, con pesos moderados.
- Cola plana de llamadas, sin tarjetas flotantes.
- Bordes finos y ausencia de sombras o gradientes.
- Paleta corporativa usada como señal, no como fondo dominante.

## Colors

El blanco y los neutros claros dominan. Los seis colores corporativos se conservan para la futura identidad del logo y se aplican con moderación en controles, estados y detalles.

La aplicación dispone de un registro escalable de temas. **Claro** es el tema inicial y **Oscuro · Zinc / Noche** usa lienzo `#09090b`, paneles `#121215`, superficies `#1a1a1e`, texto `#f4f4f5` y verde accesible `#38c160`. Cada tema define todos sus colores y recursos en un único registro; añadir uno nuevo no requiere duplicar la interfaz. La selección se aplica inmediatamente, se guarda localmente y nunca depende del modo claro u oscuro de Windows.

### Primary

- **Blanco de panel:** superficie principal de lectura y controles.
- **Verde accesible:** acción primaria y texto verde sobre fondos claros.

### Secondary

- **Lienzo claro:** separa la aplicación del contenido sin crear capas pesadas.
- **Verde suave:** selección, navegación activa y evidencia contextual.

### Tertiary

- **Verdes corporativos:** `#27953c`, `#40b73c` y `#7eca29` forman la familia de marca. El lima se reserva para marcas pequeñas y decorativas; nunca para texto normal.

### Neutral

- **Carbón y grises corporativos:** `#4f4c4c`, `#656263` y `#7a7879` sostienen texto, metadatos y estados secundarios.
- **Bordes:** `#dfe3df` y `#c8cdc8` separan regiones sin endurecer la página.

### Named Rules

**The White Field Rule.** El blanco ocupa la mayor parte de cada vista; una superficie solo cambia de tono cuando comunica agrupación o estado.

**The Logo Palette Rule.** Los colores corporativos son acentos funcionales hasta incorporar el logo; no se usan como grandes fondos decorativos.

**The Accessible Green Rule.** El texto y los indicadores funcionales usan verdes oscuros; el lima solo aparece en elementos pequeños acompañados de una etiqueta textual.

## Typography

**Display Font:** Inter.
**Body Font:** Inter, con Segoe UI como respaldo.
**Data Font:** Inter; no se introduce una segunda voz monoespaciada.

**Character:** neutral, contemporánea y muy legible. Los pesos 400–650 construyen la jerarquía sin convertir cada rótulo en un titular.

### Hierarchy

- **Headline** (600, 23px): títulos de Reportes y Configuración.
- **Title** (650, 15px): marca y encabezados principales.
- **Body** (400, 13px): resumen, transcripción y controles.
- **Label** (550, 10px, 0.3px): secciones y metadatos breves.
- **Data** (400, 11px): tiempos, rutas y valores compactos.

### Named Rules

**The One Family Rule.** Toda la aplicación usa Inter; jerarquía y datos se distinguen por tamaño, peso, color y espaciado, no por mezclar tipografías.

**The Sentence Case Rule.** Navegación, secciones y estados usan frase normal; las mayúsculas quedan limitadas a la marca SENTRY.

## Layout

La vista principal usa una cabecera de 64px, una franja de métricas de 62px y un divisor 38/62 entre cola y detalle. La cola es una lista continua con separadores de un píxel. El detalle mantiene bloques amplios y respirables, con espacios de 10–14px entre regiones.

La ventana parte de 1080×680. Por debajo de 1240px se acortan acciones secundarias y la ruta visible, conservando navegación, búsqueda y acción primaria. El desplazamiento vertical se concentra en la lista y el detalle; no debe aparecer desplazamiento horizontal.

## Elevation & Depth

No se usan sombras. La profundidad se expresa con blanco sobre lienzo gris claro, bordes sutiles y un cambio verde pálido para la selección.

### Named Rules

**The Flat Separation Rule.** Una región se distingue con espacio, un borde o un cambio tonal leve; nunca necesita los tres a la vez.

## Shapes

Los paneles usan radios de 8–10px; campos y botones, 7px; chips, 5px. Las filas de la cola no tienen radio para mantener continuidad. Todos los bordes son de un píxel.

## Components

### Buttons

- **Primary:** verde accesible, texto blanco, 36px de alto y radio de 7px.
- **Secondary:** blanco, texto carbón y borde gris claro.
- **Hover / Focus:** cambio tonal contenido y contorno verde oscuro de 2px, sin movimiento.

### Chips

- Fondo verde suave, texto verde accesible y frase normal.
- Siempre incluyen una palabra o estado; el color nunca comunica solo.

### Queue rows

- Fondo blanco, separador inferior y 100px de alto.
- La selección usa fondo verde suave y una línea verde de un píxel a la izquierda.
- No usan sombra, margen exterior ni borde redondeado.
- En colas extensas, las filas se materializan conforme entran en el área visible para conservar el desplazamiento fluido sin alterar el orden ni los filtros.

### Panels

- Fondo blanco, borde `#dfe3df`, radio de 10px y 14–20px de relleno.
- Se agrupan solo las unidades que necesitan una lectura independiente.

### Inputs / Fields

- Fondo blanco, borde gris claro, radio de 7px y altura mínima de 36px.
- El foco usa un borde verde oscuro visible; los placeholders mantienen contraste legible.
- Las credenciales usan campos enmascarados con una acción textual Mostrar/Ocultar y no se persisten sin un almacén protegido.
- La validación conserva el formulario estable, deshabilita temporalmente sus controles y comunica carga, éxito o recuperación junto al campo correspondiente.
- La conexión WinSCP/SFTP se presenta como un panel de Configuración reducido a IP, usuario y contraseña enmascarada, con una sola acción **Conectar servidor**. Puerto 22, carpeta inicial y huella SSH se resuelven internamente; el éxito siempre se comunica con texto y la contraseña se persiste únicamente cifrada con DPAPI.
- El mismo panel ofrece una búsqueda remota por teléfono o nombre. Los resultados mantienen una lista compacta con nombre, tamaño y fecha; la ruta completa aparece como ayuda y al seleccionar el archivo, sin introducir una navegación de carpetas separada.
- El origen de grabaciones es una selección explícita entre Local, NAS e Issabel. El emparejamiento se inicia al activar una base, siempre usa teléfono y fecha de `Hoja1`, y en Issabel descarga únicamente coincidencias.

### Navigation

La navegación es una fila compacta. La vista activa usa fondo verde suave y texto verde oscuro; las vistas inactivas permanecen blancas con texto gris.

El acceso **Bases** vive inmediatamente al lado del directorio en la cabecera, con el mismo control de navegación y estado seleccionado. Su vista mantiene paneles planos, una sola acción primaria, opciones avanzadas desplegables e historial tabular. El progreso solo aparece durante una conversión; el estado inicial no muestra una barra activa. El resultado o una ejecución del historial puede activarse para relacionar `Hoja1` con las grabaciones; la vista comunica el nombre de la base activa y la cantidad de teléfonos cargados.

### Línea de tiempo de evidencia

La onda usa verde oscuro para el recorrido y gris claro para lo pendiente. Una marca vertical indica el segundo sensible; admite clic y teclado, y “Ir al momento” ofrece una alternativa explícita. La transcripción mantiene una sola línea activa, avanza con el audio y permite pulsar cada bloque para navegar a su segundo; los términos sensibles conservan énfasis dentro del progreso palabra por palabra.

### Análisis persistente

La acción primaria **Analizar** vive en el extremo derecho de la cabecera. Durante una cola cambia a **Detener**, parte vacía y se llena en verde conforme termina cada llamada, mostrando además el conteo procesado. Mientras el lote está activo se detiene el audio y permanecen bloqueados la reproducción, la línea de tiempo, los saltos, la apertura del archivo y el marcado como revisada; la navegación y la observación de resultados siguen disponibles. Alertas, buzones y llamadas normales se consultan con el filtro situado a la derecha de **Llamadas detectadas**, junto al orden por prioridad, duración, fecha o nombre; sus contadores usan la misma franja de métricas. Las transcripciones usan los rótulos **Asesor** y **Cliente**, pero el panel de identidad no repite el nombre del asesor. Las coincidencias configuradas aparecen como etiquetas compactas en la tarjeta y completas en el detalle. El estado se comunica siempre con texto además del color. Sin registros reales, la auditoría y los reportes muestran un estado vacío en lugar de datos ficticios.

## Do's and Don'ts

### Do:

- **Do** permitir que el blanco domine cada vista.
- **Do** mantener una única acción primaria verde por contexto.
- **Do** conectar una alerta con su marca temporal y su línea de transcripción.
- **Do** usar separadores finos y espacio antes de añadir otro contenedor.

### Don't:

- **Don't** convertir la paleta del logo en grandes bloques saturados.
- **Don't** usar varios verdes intensos simultáneamente.
- **Don't** añadir sombras, gradientes o tarjetas para cada fragmento de información.
- **Don't** abusar de mayúsculas, tracking o pesos tipográficos altos.
