---
version: 1
slug: "app-ui-views-main-window-py"
primary_target: "app/ui/views/main_window.py"
related_targets: []
---

# Auditoría principal

Mode: Operate

Audience: operadores de calidad y supervisores que revisan llamadas durante jornadas prolongadas.

Task: detectar una llamada relevante, comprender el contexto, saltar a la evidencia exacta y marcarla como revisada con pocos pasos.

Content: datos sintéticos de cuatro llamadas, métricas de carpeta, síntesis contextual, reproductor y transcripción diarizada.

Constraints: aplicación Windows con PySide6; estructura basada en el HTML entregado; blanco dominante; la paleta corporativa suministrada funciona como acento del futuro logotipo; sin afirmar que las integraciones externas ya funcionan.

## Direction contract

THESIS: Una mesa de revisión blanca donde la señal importante conecta la cola con la transcripción; evita el dashboard de cajas pesadas y mantiene lista, evidencia y acción en una línea de trabajo silenciosa.

OWN-WORLD: Blanco y gris muy pálido forman el campo dominante. Carbón `#4f4c4c` y grises `#656263`/`#7a7879` sostienen texto y divisores; verde profundo `#27953c` identifica selección y marca, `#40b73c` confirma acciones y `#7eca29` aparece solo como detalle de evidencia. Inter aporta una voz neutra, contemporánea y legible.

STORY: El operador ve el estado del directorio, filtra llamadas, elige una incidencia y comprueba inmediatamente por qué fue señalada antes de completar su revisión. En Configuración puede sustituir proveedor, validar una clave y cargar modelos compatibles sin tocar el código.

FIRST VIEWPORT: Barra superior blanca y compacta; banda ligera de métricas y filtros; división 38/62 entre cola plana y detalle. La llamada seleccionada se distingue con un tinte casi blanco y una línea verde; el tiempo crítico y el fragmento relevante comparten el mismo acento.

FORM: Consola operativa minimalista, refinada desde la referencia del usuario. Seed key: `user-pinned-html-2026-09-14`. Interacción distintiva: el salto temporal sincroniza progreso y realce de evidencia; los demás estados cambian sin ornamentación.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
