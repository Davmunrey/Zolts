# 13 — Roadmap 90 días

Objetivo del trimestre: **3 design partners de pago ejecutando programas reales en producción, con lift medido contra holdout, y el motor de blueprints demostrando que el 4º cliente se implanta sin ingeniería a medida.**

Regla de gobierno: cada fase tiene criterios de salida binarios. No se avanza de fase con criterios incumplidos; se recorta alcance, no calidad.

---

## Fase 1 · Días 1-30 — Núcleo ejecutable y design partners

**Objetivo:** un programa completo, extremo a extremo, en producción, con un cliente real pagando.

| Workstream | Entregable | Criterio de salida |
|---|---|---|
| Datos | Esquema canónico + RLS + identity graph v0 (determinista) | 1 M de cuentas resueltas con <2% de colisiones en muestra auditada |
| Ingesta | Conectores HubSpot + Salesforce + Postgres/Snowflake (lectura + writeback) | CDC bidireccional estable 7 días sin intervención |
| Runtime | Temporal + DSL v0.1 (`trigger/audience/enrich/score/route/plays/exit`) + idempotencia | Replay de 10 k enrollments sin envíos duplicados |
| Señales | 5 señales tier A/B: funding, hiring, job change, pricing-page visit, tech install | Latencia p95 señal→acción propuesta <10 min en tier A |
| Ejecución | Email vía partner (API) + tareas a CRM | 3 k emails enviados, bounce <2% |
| Medición | Holdout determinista + tabla de outcomes | Asignación reproducible verificada con test |
| Compliance | Policy engine v0 (ES, FR, UK, US) + supresión + horas silenciosas | 100% de acciones con `policy_decision` registrada |
| Comercial | 3 design partners firmados, pilotos de pago 5 k€ / 3 meses | 15 k€ cobrados; carta de compromiso con criterios de éxito |

**Riesgo de fase:** construir producto sin cliente. Mitigación: el día 1 arranca con la venta de los pilotos, no con la arquitectura. Ningún componente entra en el plan si no lo necesita un piloto firmado.

---

## Fase 2 · Días 31-60 — Diferenciación y margen

**Objetivo:** convertir el ejecutor en plataforma: routing económico, agentes con gobierno, medición vendible.

| Workstream | Entregable | Criterio de salida |
|---|---|---|
| Waterfall router | Abstracción por campo, ≥3 proveedores, matriz de acierto por cohorte, optimizador | ≥30% de reducción de coste por contacto verificado vs. waterfall estático |
| Agentes | Researcher + Copywriter + Qualifier con verificación por procedencia y eval harness | ≥90% de afirmaciones con procedencia; golden set de 200 casos en CI |
| Deliverability | Gestión de dominios/buzones, curvas de warmup, Postmaster, circuit breakers | 0 dominios quemados; quejas <0,1% en todos los pilotos |
| Canales | LinkedIn + Ads (audiencias) + tareas de voz | 2 programas multicanal en producción |
| Medición | Dashboard de P&L por jugada + cálculo de MDE y potencia | Cada piloto recibe un informe de lift con intervalo de confianza |
| Señales | Catálogo a 15 señales + `SignalDefinition` declarativa | Un cliente añade una señal propia sin ingeniería de Zolts |
| Blueprints | 3 blueprints completos (`b2b-saas-sales-led`, `b2b-saas-plg`, `services-agency`) | Nuevo tenant configurado y ejecutando en <5 días laborables |
| Producto | Studio v1 (editor bidireccional DSL↔UI) + CLI | Un operador no técnico publica un programa sin ayuda |

**Riesgo de fase:** dispersión entre agentes, canales y routing. Mitigación: si hay que sacrificar algo, se sacrifican canales (partner cubre el hueco); el router y las evals son núcleo del moat y no se recortan.

---

## Fase 3 · Días 61-90 — Escalabilidad comercial

**Objetivo:** demostrar que el cliente 4-10 no consume ingeniería, y montar la máquina de ingresos.

| Workstream | Entregable | Criterio de salida |
|---|---|---|
| Onboarding | Perfilado de 12 dimensiones → resolver de blueprint → primer programa | Time-to-first-program <60 min autoservicio en plan Starter |
| Monetización | Medición de créditos, facturación (Stripe), techos y alertas de gasto | Facturación automática correcta 2 ciclos consecutivos; 0 disputas |
| Marketplace | Importación/exportación de programas, extracción de plantillas anonimizadas | 5 plantillas promovidas desde uso real de clientes |
| Compliance | Pack GDPR completo (DPA, ROPA, DSAR automatizado, subencargados) + arranque SOC 2 Type I | DPO de un piloto enterprise aprueba por escrito |
| Escala | Residencia UE, SSO/SAML, RBAC completo, límites por tenant | 1 contrato Scale o Enterprise firmado |
| Agentes | Gating de auto-envío por eval score en tier 2/3 | ≥60% de toques de tier 2 auto-enviados sin incidencias de marca |
| Comercial | 10 clientes de pago; motor de outbound propio de Zolts corriendo sobre Zolts | 180-250 k€ de ARR run-rate; ≥2 clientes desde outbound propio |

**Dogfooding obligatorio:** el GTM de Zolts se ejecuta íntegramente en Zolts desde el día 45. Es simultáneamente control de calidad, demo viva y fuente de plantillas.

---

## Fuera de alcance en 90 días (decisión explícita)

Infra de email propia, dialer nativo, modelo de scoring entrenado por tenant (se usa heurística calibrada), blueprints 4-10, ISO 27001, marketplace público de terceros, app móvil. Documentado para evitar la negociación de alcance en cada sprint.
