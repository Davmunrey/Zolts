# 08 — Capa de agentes IA

## Regla de oro

> **Los agentes proponen; el runtime dispone.** Ningún agente ejecuta una acción externa. Emite una `proposed_action` que atraviesa política, presupuesto y evaluación antes de materializarse. Esta separación es lo que permite vender a una empresa regulada.

## Roles

| Agente | Entrada | Salida | Riesgo dominante | Control |
|---|---|---|---|---|
| **Researcher** | Cuenta + señales | Dossier con citas y fuentes | Alucinación de hechos | Cada afirmación debe citar un span recuperado; sin cita, se descarta |
| **Strategist** | Dossier + catálogo de jugadas | Recomendación de programa/tier | Sobre-segmentación | Requiere tamaño mínimo de población y test previo |
| **Copywriter** | Dossier + persona + pruebas | Borrador de mensaje | Claims falsos, tono fuera de marca | Lista de claims permitidos + modelo de voz + eval |
| **Qualifier** | Respuestas entrantes | Clasificación + siguiente acción | Malinterpretar un "no" | Umbral alto para auto-respuesta; OOO/negativas siempre a humano |
| **Ops** | Datos crudos, esquemas | Mapeos, deduplicación, limpieza | Fusión destructiva | Merges reversibles + cola de revisión |
| **Analyst** | Resultados + holdouts | Post-mortem y recomendación | Correlación como causalidad | Solo puede concluir con datos de experimento válido |

## Anti-alucinación: verificación por procedencia

Cada mensaje generado se descompone en afirmaciones. Cada afirmación debe mapear a: (a) un span de una fuente recuperada, (b) un campo del CRM, o (c) la librería de pruebas aprobada (casos, cifras, logos). Afirmaciones sin procedencia se eliminan del borrador; si eliminarlas rompe el mensaje, se marca `needs_human`.

Esto es más restrictivo que "el LLM lo hace bien" y es exactamente lo que un CMO exige antes de dejar salir mensajes con su marca.

## Evals: el sistema de calidad

| Nivel | Qué mide | Mecanismo | Frecuencia |
|---|---|---|---|
| Unitario | Formato, longitud, campos obligatorios, ausencia de placeholders | Determinista | Cada generación |
| Factualidad | % de afirmaciones con procedencia | Verificador + LLM-judge | Cada generación |
| Marca | Adherencia a voz, claims prohibidos, prohibición de superlativos | Clasificador entrenado por tenant | Cada generación |
| Compliance | Disclosure IA, opt-out, idioma, jurisdicción | Determinista (policy engine) | Cada generación, bloqueante |
| Efectividad | Tasa de respuesta positiva vs. variante control | Experimento en producción | Continuo |
| Regresión | Golden set de 200-500 casos por tenant | CI, bloquea despliegue | Cada cambio de prompt/modelo |

**Gating de auto-envío:** un mensaje se envía sin humano solo si `eval_score ≥ umbral del tier` **y** `policy = allow` **y** el tenant ha habilitado auto-send para ese tier. Umbral por defecto: T1 nunca, T2 0.85, T3 0.90 (más alto porque nadie lo revisa a escala).

## Arquitectura de modelos

- **Router multi-modelo por tarea:** clasificación y extracción con modelos rápidos/baratos; razonamiento estratégico y copy 1:1 con modelo frontera. Objetivo de coste: <€0.02 por contacto tocado en tokens.
- **Contexto por recuperación, no por volcado:** el dossier se construye con recuperación selectiva (pgvector + filtros estructurados). Volcar todo el CRM en el prompt es caro, ruidoso y una fuga de PII innecesaria.
- **Sin PII innecesaria al modelo:** el minimizador elimina campos no requeridos por la tarea; los identificadores se tokenizan cuando el prompt no necesita el valor real.
- **Caché de prompts** para dossiers y plantillas estables: reduce coste y latencia de forma significativa en volumen.
- **Trazabilidad completa:** cada contenido guarda modelo, versión de prompt, fuentes citadas, evals y quién aprobó. Requisito de auditoría bajo AI Act.

## Interfaz agéntica hacia fuera (MCP)

Zolts expone un servidor MCP para que el equipo pueda operar desde su propio asistente: consultar segmentos, simular un programa, pedir el P&L de una jugada. **Solo lectura y simulación por defecto**; las acciones con efecto externo requieren aprobación explícita en la UI. La comodidad de "ejecuta esto por mí desde el chat" no puede saltarse el gobierno.
