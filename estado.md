# Estado actual del proyecto — Bot de tickets WhatsApp → GLPI

> **Para Claude / cualquier IA:** este documento es la FUENTE DE VERDAD del proyecto.
> Si algo acá contradice un audio, nota o archivo más viejo, **vale lo que dice acá**.
> Última actualización: 2026-06-19

---

## 1. Dónde estamos hoy

- **Fase actual (Proceso Unificado):** análisis / spikes técnicos.
- **Logro grande:** el **puente completo con GLPI está cerrado**. El bot ya puede:
  - **LEER** las categorías dinámicamente (filtradas al área 4.0).
  - **CREAR** tickets vía API (`POST /Ticket`) — probado y funcionando.
- **Próximo hito:** completar el POST para 4.0 (sumar la ubicación + setear el solicitante),
  y arrancar con la clasificación por IA.

---

## 2. Requerimientos vigentes

- El bot atiende tickets por WhatsApp y los crea en GLPI vía API REST (sin tocar la base de datos).
- **Alcance: SOLO el área 4.0 (IT y Sistemas).** Categorías que cuelgan de `4.0 > ...`.
  - `4.0 > Impresiones 3D` SÍ va. `Diseño 3D` (rama suelta) NO.
- Las categorías se consultan dinámicamente desde GLPI, no se hardcodean.
- El usuario escribe en lenguaje natural; NO elige la categoría. La IA deduce categoría, título y tipo.
- **Excepción:** la **ubicación** sí la aporta el usuario (elige su planta de Axion), porque no se
  deduce del mensaje ni del perfil. El bot se la va a preguntar (mini-elección dentro del chat).
- Diseño modular y reutilizable: filtro por área parametrizable (`GLPI_AREA_PREFIX`).

---

## 3. Decisiones tomadas (bitácora)

### 2026-06-19 — Entorno de pruebas y categoría de POST
- **Decisión:** mientras no haya sandbox, los POST de prueba se hacen en el árbol de categorías
  "Prueba" (creado a mano): id 370 (Prueba), 371 (PruebaBotMantenimiento), 372 (PruebaBot-Impresoras),
  373 (PruebaBot-Software). Se usa la **372**, que NO asigna a nadie.
- **Por qué:** crear tickets en categorías de 4.0 los asignaría al equipo real (las reglas asignan por
  categoría). El árbol "Prueba" evita ensuciar la cola de producción.
- **Pendiente:** pedir el sandbox al equipo (lo pueden armar) — para la semana que viene.

### 2026-06-17 — Alcance acotado al área 4.0 (IT/Sistemas)
- **Decisión:** el bot trabaja SOLO con las categorías del área 4.0.
- **Por qué:** es el área que desarrolla el proyecto y donde están las categorías pertinentes.
- **Nota:** hubo idas y vueltas (se evaluó atender todos los departamentos). CONFIRMADO: solo 4.0.

### 2026-06-16 — API legacy de GLPI
- **Decisión:** API legacy (`apirest.php`, initSession + token). Alternativa descartada: la nueva (OAuth2).

### 2026-06-16 — Cliente API propio (`botapi`)
- **Decisión:** cliente API propio sin restricción de IP. No usar el de localhost (filtra IP) ni
  regenerar el de "Axionlift" (puede estar en uso).

### 2026-06-16 — Perfil con permisos
- **Decisión:** la sesión cambia a Super-Admin (ID 4) con `changeActiveProfile` (el default Autoservicio
  da 403). **Pendiente:** el bot definitivo debe usar un perfil de menor privilegio.

---

## 4. Configuración técnica (cómo conectarse)

- **GLPI_URL:** `https://glpi.axionlift.com/apirest.php`
- **GLPI_APP_TOKEN:** app_token del cliente API `botapi`.
- **GLPI_USER_TOKEN:** "Token de API" de las preferencias del usuario.
- **GLPI_PROFILE_ID:** `4` (Super-Admin, provisorio).
- **GLPI_AREA_PREFIX:** `4.0` (en pruebas se usó `Prueba` para listar las categorías de prueba).
- Todo en `.env` (NUNCA al repo; ver `.gitignore`).
- Scripts: `glpi_client.py` (lee categorías), `explorar_glpi.py` (explorador GET),
  `crear_ticket.py` (POST de prueba), `revisar_plantillas.py` (audita plantillas de 4.0).

---

## 5. Hallazgos técnicos

### Categorías
- 120 categorías usables en toda la empresa; el bot usa **solo las de 4.0** (filtro `startswith("4.0")`).
- Están en **árbol jerárquico** → conviene clasificar en DOS NIVELES (rama → hoja).
- El **tipo** (incidente/solicitud) lo determina la categoría (`is_incident` / `is_request`).

### Plantillas de 4.0  (hallazgo clave del 2026-06-19)
- Las **73 categorías de 4.0** usan todas la misma plantilla: **"Plantilla IT & Sistemas" (id 2)**.
- Esa plantilla marca 4 campos como **OBLIGATORIOS**:
  1. **Título** (`name`) — ya se manda.
  2. **Descripción** (`content`) — ya se manda.
  3. **Categoría** (`itilcategories_id`) — ya se manda.
  4. **Ubicación** (`locations_id`) — **FALTA**. Es el único campo nuevo a sumar para 4.0.
- La **ubicación** la elige el usuario (su planta de Axion). Es un `Location` (id numérico); las
  opciones se listan con un GET de `Location`. El bot se la va a preguntar.
- (La categoría de prueba 372 usa la plantilla "Default", sin obligatorios — por eso el POST mínimo
  funcionó ahí. En 4.0 hay que sumar la ubicación.)

### Anatomía del POST (probado el 2026-06-19, ticket de prueba creado OK)
- **El bot manda:** `name`, `content`, `itilcategories_id`, `type` (+ `locations_id` para 4.0).
- **GLPI completa solo:** `id`, fechas, `status` (queda "nuevo"), `priority` (calcula urgency × impact),
  SLAs, estadísticas. Confirmado: las reglas se aplican a tickets creados por API.
- **El `content` se guarda como HTML.** En la prueba se mandó texto plano (funciona); para producción
  conviene envolverlo en HTML.
- **El solicitante (`requester`) es relacional** (`Ticket_User`), no un campo simple. En la prueba no se
  seteó (quedó la cuenta de la sesión como quien registra). Falta probar setearlo.

---

## 6. Pendientes / temas abiertos

- **Completar el POST para 4.0:** sumar `locations_id` (ubicación) y probar setear el **solicitante**.
- **Analizar el formulario de 4.0** (`formularioSistemas4_0.json`, exportado de GLPI) para entender la
  lógica completa: qué pregunta, cómo mapea a campos del ticket, qué destino arma.
- **Ubicación:** GET de `Location` para ver las plantas y armar las opciones que el bot ofrece.
- **Identificación del usuario:** mapear número de teléfono → usuario de GLPI/AD (plan B: preguntar).
- **Clasificación por IA:** empezar simple (LLM + categorías de 4.0, dos niveles). Usar tickets
  históricos como banco de pruebas para medir precisión. Plan B para dudas: confirmar con el usuario.
- **Entrada por WhatsApp:** webhook FastAPI + Meta Cloud API.
- **Sandbox:** pedirlo al equipo (semana que viene).
- **Perfil de menor privilegio** para el bot definitivo.
- **Adjuntos (fotos/PDF):** segunda etapa (`Document` / `Document_Item`).
- **Trazabilidad:** crear un `requesttypes_id` "WhatsApp Bot".

---

## 7. Cosas que YA descarté (no proponer de nuevo)

- Tocar la base de datos MariaDB directamente → NO, solo vía API REST.
- Atender todos los departamentos → NO, solo el área 4.0 (IT/Sistemas).
- Que el usuario elija la categoría como en el formulario → NO, la deduce la IA (pero la ubicación SÍ
  la elige el usuario).
- Postear de prueba en categorías de 4.0 → NO (se asignan al equipo real); usar el árbol "Prueba".
- Usar el app_token del cliente "full access from localhost" → NO sirve fuera del server.
- Subir `.env` o archivos con datos internos al repo público → NO.

---

## 8. Documentación del proyecto

- `estado.md` — este documento (decisiones, estado, hallazgos).
- `analisis_api_glpi.md` — resultados de la exploración GET y deducciones de diseño.
- `anexo_ejecucion.md` — registro de salidas de los scripts (anonimizado).
- `README.md` — qué es el proyecto y cómo correrlo.
- Repo: github.com/cervetade/bot-tickets-glpi (público; `.env` y datos internos quedan fuera).
