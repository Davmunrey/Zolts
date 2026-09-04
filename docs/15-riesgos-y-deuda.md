# 15 — Riesgos, deuda y mitigaciones

## Matriz de riesgos (ordenada por daño esperado)

| # | Riesgo | Prob. | Impacto | Mitigación estructural | Señal temprana |
|---|---|---|---|---|---|
| 1 | **Commoditización por incumbentes** (HubSpot/Salesforce empaquetan lo mismo) | Alta | Alto | No competir en registro; el valor está en runtime + política + incrementalidad, áreas donde los incumbentes son lentos por arquitectura y por conflicto con su base instalada | Anuncios de "AI agents" nativos en su roadmap |
| 2 | **Clay/Unify se mueven al mismo terreno** | Alta | Alto | Ventaja de 12-18 meses en compliance jurisdiccional y experimentación; ambos requieren reescribir su núcleo, no añadir una feature | Aparición de holdouts o policy packs en su producto |
| 3 | **Colapso de la deliverability del canal email** | Media | Muy alto | Multicanal desde el día 1; capacidad de envío como inventario gestionado; peso creciente de señales de producto e inbound | Caída sostenida de tasa de respuesta en todos los tenants |
| 4 | **Shock de proveedores de datos** (precio, política, cierre de API) | Media | Alto | Abstracción por campo, mínimo 2 proveedores por campo crítico, precios como configuración, pass-through contractual al cliente | Cambio de términos o de precios de un proveedor con >25% de share |
| 5 | **Sanción o denuncia GDPR a un cliente por datos enrutados** | Baja-Media | Muy alto | Base legal obligatoria por acción, minimización, DPAs firmados, auditoría completa, packs conservadores por defecto (bloquear antes que permitir) | Denuncias sectoriales contra proveedores de enriquecimiento |
| 6 | **Incidente de marca por contenido IA** (claim falso a una cuenta enterprise) | Media | Alto | Verificación por procedencia, gating por evals, tier 1 siempre humano, librería de claims aprobados | Caída del eval de factualidad tras cambio de modelo |
| 7 | **Trampa de servicios** (cada cliente exige implantación a medida) | Alta | Medio-Alto | Blueprints + overlays; techo duro de servicios al 20% del ingreso; extracción de plantillas | Horas de ingeniería por cliente creciendo mes a mes |
| 8 | **Concentración en design partners** | Alta | Medio | Máx. 25% de ARR por cliente a partir del mes 9; diversificar arquetipos desde el mes 4 | Un cliente >30% del ARR |
| 9 | **Ciclo de venta más largo del previsto** (comprador CFO) | Media | Medio | Land con Growth self-serve y expandir; no depender de enterprise para sobrevivir | Ciclo mediano >75 días en mid-market |
| 10 | **LinkedIn u otra plataforma cierra el acceso automatizado** | Media | Medio | Nunca depender de automatización no soportada en rutas críticas; el canal es sustituible en el DSL | Cambios de ToS o oleadas de restricciones |

## Deuda técnica: cuáles se aceptan y cuáles no

| Deuda | ¿Aceptable? | Condición |
|---|---|---|
| Scoring heurístico antes que modelo entrenado | Sí | Interfaz `ScoreModel` estable desde el día 1 para sustituirlo sin tocar programas |
| Email vía partner en vez de infra propia | Sí | Abstracción de canal desde el día 1; migración planificada, no improvisada |
| Un solo blueprint completo en fase 1 | Sí | El mecanismo de overlays debe existir aunque solo haya un blueprint |
| Identity graph solo determinista al inicio | Sí | El esquema debe soportar confianza probabilística desde el principio |
| **Saltarse la idempotencia** | **No** | Un envío duplicado es un fallo de confianza irrecuperable |
| **Saltarse el registro de decisiones de política** | **No** | Sin auditoría no hay enterprise, y es irrecuperable retroactivamente |
| **Programas sin versionado** | **No** | Es la razón de existir del producto |
| **Evals opcionales para auto-envío** | **No** | Un incidente de marca cuesta más que todo el ARR del primer año |

## Deuda organizativa

| Patrón | Consecuencia | Contramedida |
|---|---|---|
| Ingeniería atendiendo tickets de clientes | Roadmap secuestrado | Rol dedicado de Forward-Deployed GTM Engineer, con presupuesto de tiempo acotado y obligación de convertir cada intervención en configuración reutilizable |
| Fundador como único vendedor más allá del mes 9 | Techo de crecimiento | Contratar AE #1 en el mes 7 con playbook ya documentado y validado |
| Sin dueño de la calidad de datos | El identity graph se degrada silenciosamente | Un owner nombrado + métricas de calidad en el dashboard interno semanal |
| Decisiones de arquitectura sin registrar | Reapertura eterna de debates | ADRs obligatorios (ver [02](02-arquitectura.md)) |

## Riesgo de commoditización: la pregunta honesta

Si en 24 meses cualquiera puede construir "un agente que enriquece y envía emails" con tres llamadas a un LLM, ¿qué queda de Zolts?

Queda lo que no se copia con un prompt:
1. **El dataset de outcomes** — qué señal, en qué segmento, con qué mensaje, produjo qué resultado, con control experimental. Se acumula solo ejecutando, y no se puede comprar.
2. **La matriz de acierto real por proveedor y cohorte** — se construye solo con volumen real y verdad-terreno.
3. **La reputación de envío** — es física, tarda meses y no se transfiere.
4. **Los packs de política validados por DPOs** — coste de adopción y responsabilidad legal que nadie regala.
5. **La lógica GTM versionada de cada cliente** — su propio conocimiento operativo, acumulado en el sistema.

Corolario estratégico: **hay que ejecutar volumen real cuanto antes**, aunque sea poco rentable al principio. El moat se acumula con la ejecución, no con la arquitectura. Cada mes de retraso en tener clientes ejecutando es un mes de foso no construido.
