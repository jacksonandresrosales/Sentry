# 📋 ESPECIFICACIONES TÉCNICAS DEL SISTEMA (SPECS)
**Proyecto:** Sentry (Centinela) - Sistema de Auditoría y Detección de Palabras Clave en Llamadas  
**Rama:** `feat/Local-Ecuaconexion-Jackson`  
**Fecha:** Septiembre 2026  
**Tipo de Aplicación:** Aplicación de Escritorio Nativa (Windows)  
**Entorno de Uso:** Uso interno empresarial (sin login inicial, diseñado para operador de calidad)  
**Confidencialidad:** Privada / Propiedad Exclusiva de la Empresa  

---

## 1. OBJETIVO GENERAL
Automatizar la auditoría de grabaciones telefónicas generadas por el conmutador de la empresa mediante un sistema de escritorio que:
1. Monitoree un **directorio configurable** donde se depositan los audios (`.wav`, `.mp3`).
2. Transcriba el audio a texto en español utilizando **Deepgram Nova-3**, identificando quién habla (Agente vs. Cliente) y protegiendo datos sensibles (tarjetas, identificaciones) de manera nativa.
3. Detecte palabras clave críticas (con énfasis primordial en *"demanda"*, *"abogado"*, *"denuncia"*, *"queja"*, *"cancelación"*).
4. Analice el contexto mediante **Google Gemini 1.5 Flash** para validar si la palabra *"demanda"* es una amenaza legal real o un falso positivo comercial, asignando un nivel de riesgo y redactando un resumen en 2 líneas.
5. Almacene los datos estructurados en una base de datos local **SQLite**.
6. Presente una interfaz de escritorio **minimalista y sobria** (en escala de grises, donde el color rojo se utiliza de forma **exclusiva** para resaltar términos sensibles), con un reproductor que permita saltar directamente al segundo exacto donde se pronunció la palabra crítica y exportar reportes a Excel con un clic.

---

## 2. VOLUMETRÍA Y ANÁLISIS FINANCIERO (RENTABILIDAD)

### Métricas de Operación
* **Volumen diario:** ~20 llamadas por día.
* **Duración promedio por llamada:** Entre 40 segundos y 1 minuto (raras ocasiones hasta 10 minutos).
* **Volumen mensual (30 días):** 600 llamadas al mes (~10 horas de audio en total).
* *(Días laborables / 22 días):* ~440 llamadas al mes (~7.3 horas de audio).

### Desglose de Costos de APIs por Mes (600 llamadas)
| Componente | Proveedor / Modelo | Tarifa Oficial | Costo Mensual Estimado |
| :--- | :--- | :--- | :--- |
| **Transcripción (STT)** | **Deepgram Nova-3** | $0.0043 / minuto | **~$2.58 USD** |
| **Diarización (Agente/Cliente)** | Deepgram Nova-3 | Incluida en Batch | **$0.00 USD** |
| **Enmascarado PII (Privacidad)** | Deepgram Nova-3 | Incluido nativo | **$0.00 USD** |
| **Análisis Contextual y Resumen (LLM)** | **Google Gemini 1.5 Flash** | $0.075 / 1M tokens in | **~$0.04 USD** |
| **Base de Datos y Software Local** | SQLite + PySide6 | Embebido / Local | **$0.00 USD** |
| **TOTAL OPERATIVO MENSUAL** | — | — | **~$2.62 USD / mes** |

> 💰 **Costo unitario por llamada:** **~$0.0043 USD** (menos de medio centavo de dólar por llamada auditada completa).  
> 🎁 **Crédito de Bienvenida:** Deepgram otorga $200 USD gratuitos al registrarse, permitiendo operar el sistema a costo $0 durante los primeros años con este volumen.

---

## 3. ARQUITECTURA TÉCNICA Y STACK TECNOLÓGICO

```
+-----------------------------------------------------------------------------------+
|                            CAPA DE PRESENTACIÓN (UI)                              |
|          PySide6 (Qt 6 para Python) • QtMultimedia • Empaquetado PyInstaller     |
+-----------------------------------------------------------------------------------+
                                         │
                                         ▼
+-----------------------------------------------------------------------------------+
|                        CAPA DE NEGOCIO Y ORQUESTACIÓN                             |
|  Directorio Watcher • Máquina de Estados • Manejo de Reintentos (Tenacity)       |
+-----------------------------------------------------------------------------------+
        │                                 │                                 │
        ▼                                 ▼                                 ▼
+─────────────────+             +──────────────────+             +──────────────────+
|  TRANSCRIPCIÓN  |             | INTELIGENCIA LLM |             |   PERSISTENCIA   |
| Deepgram Nova-3 |             | Gemini 1.5 Flash |             |     SQLite 3     |
| • Diarize=True  |             | • Strict JSON    |             | • Tablas: calls, |
| • Redact PII    |             | • Context Check  |             |   keyword_hits,  |
| • Keyterms      |             | • Resumen 2 lín  |             |   app_settings   |
+─────────────────+             +──────────────────+             +──────────────────+
```

### Tecnologías Seleccionadas
* **Lenguaje:** Python 3.10+
* **Interfaz Gráfica:** **PySide6 (Qt 6)**. Garantiza rendimiento nativo en Windows, estabilidad multihilo (la UI nunca se congela mientras se procesan llamadas) y compatibilidad directa con reproductores multimedia.
* **Transcripción de Audio:** **Deepgram SDK (`nova-3`)**.
  * Idioma: `es-419` (Español Latinoamericano).
  * Diarización: `diarize=True` (separa `speaker 0` y `speaker 1`).
  * Enmascaramiento: `redact=["pci", "ssn", "numbers"]`.
  * Sensibilidad prioritaria: `keywords=["demanda:2.0", "abogado:2.0", "denuncia:2.0", "queja:1.5", "cancelación:1.5"]`.
* **Análisis de Texto y Clasificación:** **Google Gemini 1.5 Flash** (`google-genai`).
  * Formato forzado: `response_mime_type="application/json"`.
* **Base de Datos:** **SQLite 3** (`data/db/sentry_audit.db`).
* **Reproducción:** **QtMultimedia** (`QMediaPlayer` + `QAudioOutput`).

---

## 4. ESPECIFICACIÓN DE LA INGESTA (DIRECTORIO CONFIGURABLE)

El sistema **no depende de una ruta rígida de red**, sino que ofrece flexibilidad total:
1. **Configuración de Ruta:** El usuario puede definir o cambiar el directorio en cualquier momento desde la interfaz mediante un cuadro de texto o el botón *"Examinar..."* (diálogo nativo de carpetas de Windows).
2. **Ubicaciones admitidas:**
   * Carpetas locales (ej. `C:\Grabaciones\Hoy`, `D:\Audios`).
   * Unidades de red compartidas o NAS (ej. `\\Servidor\Llamadas` o `Z:\Grabaciones`).
   * Carpetas sincronizadas en la nube (Google Drive, OneDrive, Dropbox).
3. **Control de Archivo Cerrado (Integridad):**
   * Antes de procesar un archivo nuevo, el sistema comprueba su tamaño y espera 3 segundos. Si el tamaño no varía, se asume que el conmutador terminó de grabarlo y se procede a la ingesta.
4. **Formatos Soportados:** `.wav`, `.mp3`, `.ogg`, `.m4a`, `.gsm`.

---

## 5. MODELO DE BASE DE DATOS (ESQUEMA SQLITE)

### Tabla `calls` (Registro Principal de Llamadas)
* `id` INTEGER PRIMARY KEY AUTOINCREMENT
* `filename` TEXT NOT NULL (Nombre del archivo de audio)
* `file_path` TEXT NOT NULL (Ruta completa en el directorio)
* `file_hash` TEXT UNIQUE (Hash SHA-256 para evitar reprocesar archivos duplicados)
* `duration_seconds` INTEGER (Duración en segundos)
* `status` TEXT CHECK(status IN ('PENDIENTE', 'TRANSFIRIENDO', 'ANALIZANDO', 'COMPLETADO', 'ERROR'))
* `transcript` TEXT (Transcripción con etiquetas de agente y cliente)
* `summary` TEXT (Resumen ejecutivo de 2 líneas)
* `sentiment` TEXT (POSITIVO, NEUTRAL, MOLESTO, CRÍTICO)
* `risk_level` TEXT CHECK(risk_level IN ('BAJO', 'MEDIO', 'ALTO', 'CRÍTICO'))
* `has_sensitive_keyword` BOOLEAN DEFAULT 0
* `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
* `processed_at` DATETIME

### Tabla `keyword_hits` (Ocurrencias de Palabras Sensibles)
* `id` INTEGER PRIMARY KEY AUTOINCREMENT
* `call_id` INTEGER REFERENCES calls(id) ON DELETE CASCADE
* `keyword` TEXT NOT NULL (Ej: "demanda", "abogado")
* `speaker` TEXT (Agente o Cliente)
* `timestamp_seconds` REAL NOT NULL (Segundo exacto donde se pronunció)
* `context_snippet` TEXT (Frase donde se dijo la palabra)
* `is_risk_validated` BOOLEAN (Validación del LLM si representa riesgo real)

### Tabla `app_settings` (Configuración Local)
* `key` TEXT PRIMARY KEY
* `value` TEXT

---

## 6. LÓGICA DE DETECCIÓN Y DESAMBIGUACIÓN ("DEMANDA")

Para evitar falsas alarmas, el sistema ejecuta un proceso de validación en 2 fases:

1. **Fase 1: Detección Rápida de Términos:**
   * Búsqueda de palabras clave configurables: `demanda`, `abogado`, `denuncia`, `defensoría`, `queja`, `cancelar servicio`, `estafa`.
2. **Fase 2: Validación Semántica con Gemini 1.5 Flash:**
   * **Prompt del Sistema:** Analiza si la mención de *"demanda"* corresponde a una amenaza de acción legal por parte del cliente o a una conversación comercial cotidiana.
   * **Ejemplo de Riesgo Crítico (Alerta Roja):**
     > *"Si hoy no me devuelven mi dinero voy a presentar una demanda con mi abogado."*  
     > ➔ `has_sensitive_keyword: true`, `risk_level: "CRÍTICO"`.
   * **Ejemplo de Falso Positivo (Normal):**
     > *"Este producto tiene mucha demanda en el mercado."*  
     > ➔ `has_sensitive_keyword: false`, `risk_level: "BAJO"`.

---

## 7. ESPECIFICACIÓN DE INTERFAZ DE USUARIO (UI / UX)

### Principios de Diseño
* **Minimalismo y Sobriedad:** Fondo oscuro en tonos neutros (Zinc 950 / Zinc 900), tipografía limpia y bordes sutiles. Sin degradados llamativos ni colores distractores.
* **Uso Exclusivo del Color Rojo:** El color rojo (`#ef4444`) se reserva **únicamente** para marcar palabras sensibles, llamadas con riesgo crítico y el punto de salto del reproductor.

### Pantallas de la Aplicación
1. **Pantalla 1: Auditoría de Llamadas (Principal - Panel Dividido):**
   * **Barra Superior:** Selector dinámico de directorio con botón *"Cambiar"*, pestañas de navegación y botón *"Escanear Carpeta"*.
   * **Subheader:** Métricas en escala de grises (Total de llamadas, llamadas normales) con contador rojo para llamadas con palabras sensibles. Buscador y filtro rápido (*Todas / Solo Alertas*).
   * **Panel Izquierdo (Bandeja):** Lista vertical de llamadas. Las llamadas con palabras sensibles llevan un borde y etiqueta roja discreta con la palabra encontrada y el cliente anonimizado (`+593 98***123`).
   * **Panel Derecho (Detalle):**
     * Ficha de la llamada (Archivo, Agente, Cliente, Fecha y Duración).
     * Síntesis contextual de IA (Resumen de 2 líneas).
     * **Reproductor de audio integrado** con el botón estrella:  
       🎯 **`[Ir al segundo 00:38 ("demanda")]`** (posiciona el cursor de reproducción exactamente en el segundo donde se pronunció el término sensible).
     * **Transcripción diarizada:** Conversación organizada por interlocutor con la palabra sensible resaltada en rojo.
     * Botones de acción: *Marcar como Auditada*, *Escalar a Supervisor*, *Exportar Excel*.
2. **Pantalla 2: Reportes y Métricas:**
   * Conteo acumulado mensual de llamadas analizadas y palabras sensibles detectadas.
   * Botón para exportar reporte consolidado a Excel (`.xlsx`).
3. **Pantalla 3: Configuración:**
   * Selección y persistencia del directorio de grabaciones.
   * Lista editable de palabras sensibles a vigilar.
   * Claves de API de Deepgram y Gemini (almacenadas localmente de forma segura).

---

## 8. POLÍTICA DE SEGURIDAD, PRIVACIDAD Y LICENCIA

1. **Privacidad de Datos Sensibles:**
   * **Zero Data Retention (ZDR):** Se configuran las peticiones de API para garantizar que ni Deepgram ni Google almacenen o re-entrenen modelos con los audios o transcripciones de la empresa.
   * **Enmascaramiento PII:** Cualquier número de tarjeta bancaria, cuenta o identificación es sustituido automáticamente por `[DATOS_PROTEGIDOS]`.
2. **Tolerancia a Fallos:**
   * Reintentos automáticos con retroceso exponencial (*exponential backoff*) ante micro-cortes de conexión.
   * Si la app se cierra o se reinicia la PC, la base de datos retoma las llamadas en estado `PENDIENTE` sin perder información ni duplicar registros.
3. **Licencia:**
   * Software confidencial y propietario (`All Rights Reserved`) para uso exclusivo de la empresa. No es código abierto.
