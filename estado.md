# Estado actual del proyecto — Bot de tickets WhatsApp → GLPI

> **Para Claude / cualquier IA:** este documento es la FUENTE DE VERDAD del proyecto.
> Si algo acá contradice un audio, nota o archivo más viejo, **vale lo que dice acá**.
> Última actualización: 2026-06-16

---

## 1. Dónde estamos hoy

- **Fase actual (Proceso Unificado):** análisis / primeros spikes técnicos.
- **Logro del día:** spike de LECTURA de la API de GLPI **completo y funcionando**.
  El bot ya se conecta, autentica, cambia de perfil y lee las categorías dinámicamente.
- **Próximo hito:** spike de POST (crear un ticket de prueba en GLPI).

---

## 2. Requerimientos vigentes

- El bot atiende tickets por WhatsApp y los crea en GLPI vía API REST (sin tocar la base de datos).
- **El bot atiende tickets de TODOS los departamentos** (Marketing, Mantenimiento, Ingeniería,
  Sistema 4.0, etc.). "Sistema 4.0" es el ÁREA que desarrolla el proyecto, NO el alcance de las
  categorías. (Corrección del brief inicial — antes se entendía mal como "solo área 4.0".)
- Las categorías se consultan dinámicamente desde GLPI, no se hardcodean.
- El usuario escribe en lenguaje natural; NO elige la categoría (a diferencia del formulario de GLPI).
  La IA deduce categoría, título y tipo a partir del mensaje.
- Diseño modular y reutilizable (el filtro por área quedó parametrizable por si se necesita acotar).

---

## 3. Decisiones tomadas (bitácora)

### 2026-06-16 — Uso la API legacy de GLPI
- **Decisión:** conectar el bot por la API legacy (`apirest.php`, initSession + token).
- **Por qué:** más simple de arrancar y mejor documentada para crear tickets.
- **Alternativa descartada:** la API nueva (`api.php/v1`, OAuth2). Queda como posible migración futura.

### 2026-06-16 — Cliente API propio para el bot
- **Decisión:** creé un cliente API nuevo en GLPI llamado `botapi`, sin restricción de IP
  (campos IPv4 vacíos), y uso su app_token.
- **Por qué:** el cliente "full access from localhost" solo acepta llamadas desde el server de GLPI
  (no sirve corriendo desde mi compu), y el cliente "Axionlift" podía estar en uso por otras
  integraciones (no conviene regenerarle el token).
- **Alternativa descartada:** reusar/regenerar tokens de clientes existentes.

### 2026-06-16 — El bot opera con un perfil con permisos
- **Decisión:** la sesión cambia al perfil Super-Admin (ID 4) tras el login, vía `changeActiveProfile`.
- **Por qué:** el perfil por defecto (Autoservicio) da 403 al listar categorías; no tiene permisos.
- **Pendiente:** el perfil DEFINITIVO del bot debe ser de menor privilegio (solo leer categorías/usuarios
  y crear tickets), por principio de menor privilegio. Super-Admin es solo para explorar.

---

## 4. Configuración técnica (cómo conectarse)

- **GLPI_URL:** `https://glpi.axionlift.com/apirest.php`
- **GLPI_APP_TOKEN:** app_token del cliente API `botapi`.
- **GLPI_USER_TOKEN:** "Token de API" generado en las preferencias del usuario.
- **GLPI_PROFILE_ID:** `4` (Super-Admin, provisorio para explorar).
- **GLPI_AREA_PREFIX:** vacío (trae todas las áreas).
- Estos valores viven en `.env` (NUNCA se suben al repo; ver `.gitignore`).
- Scripts: `src/glpi_client.py` (cliente + lectura de categorías) y `src/explorar_glpi.py` (explorador GET).

---

## 5. Hallazgos técnicos (de explorar la API)

- Hay **120 categorías** usables para tickets (descartando las "contenedoras" de nivel 1, que no sirven
  para crear tickets directamente).
- Las categorías están en **árbol jerárquico** (ej. `4.0 > Falla de equipo > Impresora`).
  → Esto habilita clasificar en DOS NIVELES (rama grande → hoja), más preciso que elegir entre 120.
- El **tipo** de ticket (incidente=1 / solicitud=2) lo determina la categoría (`is_incident` / `is_request`).
  Una vez elegida la categoría, el tipo casi sale solo.
- Varias categorías tienen **plantillas** asociadas (`tickettemplates_id_*`), que pueden imponer
  **campos obligatorios** al crear el ticket. A verificar en el spike de POST.

### Estructura de un ticket (analizado sobre el Ticket #1234)
Un ticket tiene ~40 campos, pero el bot manda solo un puñado:

- **El bot SÍ manda:** `name` (título), `content` (descripción, se guarda como HTML),
  `itilcategories_id` (categoría), `type` (incidente/solicitud).
- **Opcionales que el bot puede mandar:** `entities_id`, `urgency`, `impact`,
  `requesttypes_id` (canal), `locations_id`.
- **Lo completa GLPI solo (NO mandar):** `id`, fechas (`date`, `date_creation`, etc.), `status`
  (al crear queda "nuevo"), `priority` (la calcula con urgency × impact), SLAs/OLAs, estadísticas.
- **El solicitante es relacional**, no un campo simple: vive en `Ticket_User`.
  `users_id_recipient` = quién REGISTRA el ticket (será la cuenta del bot).
  El `requester` (el empleado que escribió por WhatsApp) se setea aparte al crear.

### Flujo mensaje → ticket (el "traductor")
El usuario aporta solo: su **mensaje** en lenguaje natural (+ su número de teléfono, que viene solo
con WhatsApp; + adjuntos opcionales). El bot fabrica el resto:
- mensaje → IA resume → `name`
- mensaje (tal cual) → `content`
- mensaje → IA clasifica → `itilcategories_id`
- categoría → `type`
- número de teléfono → identificación → `requester`

---

## 6. Pendientes / temas abiertos

- **Spike de POST:** crear un ticket de prueba (próximo paso). Verificar campos obligatorios por plantilla.
- **Identificación del usuario:** mapear número de teléfono → usuario de GLPI/AD.
  Plan B si no hay match: preguntar nombre y área. (Desafío central.)
- **Diseño de la clasificación por IA:** empezar simple (LLM tipo Gemini + lista de categorías,
  clasificación en dos niveles). Usar tickets históricos como ejemplos y, sobre todo, como banco de
  pruebas para MEDIR la precisión. Plan B para casos dudosos: confirmar con el usuario o categoría
  "sin clasificar" para revisión humana. No hace falta entrenar un modelo desde cero.
- **Perfil definitivo del bot** (menor privilegio).
- **Adjuntos (fotos/PDF):** segunda etapa. En GLPI son "Documentos" (`Document` / `Document_Item`):
  hay que bajar el archivo de WhatsApp, subirlo a GLPI y asociarlo al ticket.
- **Idea de trazabilidad:** crear un `requesttypes_id` propio tipo "WhatsApp Bot" para identificar
  los tickets creados por el bot.

---

## 7. Cosas que YA descarté (no proponer de nuevo)

- Tocar la base de datos MariaDB directamente → NO, solo vía API REST.
- Limitar el bot al área "Sistema 4.0" → NO, atiende todos los departamentos.
- Que el usuario elija la categoría como en el formulario de GLPI → NO, la deduce la IA.
- Usar el app_token del cliente "full access from localhost" → NO sirve corriendo fuera del server.
