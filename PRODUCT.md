# Product

<!-- impeccable:product-schema 1 -->

## Platform

windows-desktop

## Stack

Python 3.10+ con PySide6 (Qt 6), definido en la documentación existente del repositorio.

## Users

Operadores de calidad y supervisores internos que revisan grabaciones de atención al cliente durante su jornada de trabajo en Windows.

## Product Purpose

Sentry centraliza la auditoría de llamadas: permite localizar grabaciones, revisar transcripciones diarizadas, identificar términos sensibles y entender su contexto para completar la revisión.

## Positioning

La aplicación conecta cada alerta con el fragmento exacto de audio y su contexto, para que el operador pueda validar riesgos sin escuchar la llamada completa.

## Operating Context

Uso interno en escritorio, con grabaciones almacenadas en carpetas locales, unidades compartidas o NAS. El flujo principal es escanear, filtrar, seleccionar, escuchar y revisar llamadas.

## Capabilities and Constraints

- Audio desde carpeta local, NAS o Issabel con reproducción y análisis bajo demanda. El flujo relaciona teléfono y fecha de `Hoja1` con archivos WAV/MP3 de nombre `q-<cola>-<teléfono>-<AAAAMMDD>-...`. En Issabel consulta directamente las carpetas de día, filtra por teléfonos en el servidor y descarga solo coincidencias; transcribe, clasifica en alerta, buzón o normal, sincroniza el texto con la reproducción, guarda cada resultado en SQLite y reutiliza cachés por huella/configuración para evitar consumos repetidos. La lista admite filtros y orden por prioridad, duración, fecha o nombre. La vista Bases integra la transformación CSV/Excel al modelo NO. La auditoría y los reportes no muestran datos ficticios.
- La configuración permite sustituir proveedor, modelo y claves de transcripción y análisis, además de preparar un acceso WinSCP/SFTP simplificado a IP, usuario y contraseña; puerto 22, carpeta inicial y huella SSH se gestionan automáticamente. La búsqueda remota recorre las carpetas de Issabel y localiza WAV/MP3 por teléfono o parte del nombre. Las claves y la contraseña guardadas se cifran mediante DPAPI y solo el mismo usuario de Windows puede recuperarlas; nunca se almacenan en texto plano.
- Estructura de referencia: barra superior, navegación entre Auditoría, Reportes y Configuración, métricas, lista maestra y panel de detalle.
- Idioma principal: español.
- Datos sensibles deben aparecer anonimizados.
- La interfaz debe seguir siendo utilizable con teclado y en ventanas de escritorio reducidas.

## Brand Commitments

- Nombre: Sentry.
- Paleta del futuro logotipo: `#27953c`, `#40b73c`, `#4f4c4c`, `#7eca29`, `#656263`, `#7a7879`. Debe funcionar como acento de marca, no como fondo dominante.
- La referencia HTML entregada por el usuario es la autoridad para la estructura de la primera pantalla.
- Estética operativa y minimalista, con tema claro predeterminado y tema oscuro Zinc / Noche seleccionable, ambos independientes de la configuración visual de Windows.

## Evidence on Hand

- `README.md`, `ARQUITECTURA.md` y `SPECS.md` describen el alcance esperado.
- La referencia HTML suministra contenido y comportamiento de demostración.
- No hay todavía grabaciones reales, identidad gráfica final ni servicios conectados; no deben inventarse como capacidades terminadas.

## Product Principles

- Mostrar primero el riesgo y el contexto necesario para decidir.
- Reducir el tiempo entre alerta, evidencia y acción.
- Reservar los colores más brillantes para estados accionables.
- Mantener controles familiares y legibles durante jornadas largas.
- Distinguir siempre los datos de demostración de los datos reales.

## Accessibility & Inclusion

Contraste suficiente, foco visible, navegación por teclado, objetivos de interacción cómodos y estados que no dependan únicamente del color.
