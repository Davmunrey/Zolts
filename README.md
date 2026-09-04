# Zolts — GTM Operating System

**BLUF:** Zolts es el *sistema de ejecución* de Go-To-Market: una capa componible, nativa de warehouse y gobernada, que convierte **señales → decisiones → jugadas → ingresos medidos por incrementalidad**. No es otro CRM (sistema de registro) ni otro AI SDR (commodity). Es el runtime donde vive la lógica GTM de la compañía, versionada, testeable y auditable.

| Dimensión | Posición |
|---|---|
| Categoría | GTM Operating System (runtime de ejecución + gobierno) |
| Primitiva central | **GTM Program** = `señal → segmento → enriquecimiento → decisión → jugada → canal → medición` |
| Adaptabilidad | **Blueprints** por arquetipo de empresa (config, no fork de código) |
| Moat | Loop de outcomes propietario + capa de routing de datos + motor de políticas por jurisdicción |
| Modelo económico | Plataforma + asientos + créditos (tarifa en tres partes), GM objetivo 75-80% |

## Índice del plan

| # | Documento | Qué resuelve |
|---|---|---|
| 00 | [Resumen ejecutivo](docs/00-resumen-ejecutivo.md) | Tesis, trade-offs, decisión |
| 01 | [Mercado y posicionamiento](docs/01-mercado-y-posicionamiento.md) | Categoría, competencia, wedge, Build/Buy/Partner |
| 02 | [Arquitectura de producto](docs/02-arquitectura.md) | Capas, runtime, stack, ADRs |
| 03 | [Modelo de datos](docs/03-modelo-de-datos.md) | Entidades canónicas, identity graph, multi-tenancy |
| 04 | [DSL de Programas GTM](docs/04-dsl-programas-gtm.md) | Config-as-code, versionado, testing |
| 05 | [Blueprints y adaptabilidad](docs/05-blueprints-adaptabilidad.md) | 10 arquetipos de empresa, resolver, overlays |
| 06 | [Librería de señales](docs/06-libreria-de-senales.md) | Catálogo, decay, SLA de time-to-touch |
| 07 | [Motor de datos y waterfall](docs/07-motor-de-datos-waterfall.md) | Routing multi-proveedor, optimizador de coste |
| 08 | [Capa de agentes IA](docs/08-capa-de-agentes-ia.md) | Roles, guardrails, evals, gating de auto-envío |
| 09 | [Ejecución y deliverability](docs/09-ejecucion-y-deliverability.md) | Canales, capacidad de envío, reputación |
| 10 | [Medición e incrementalidad](docs/10-medicion-e-incrementalidad.md) | Holdouts por defecto, P&L por jugada |
| 11 | [Compliance y gobierno](docs/11-compliance-y-gobierno.md) | GDPR/ePrivacy/AI Act, policy engine |
| 12 | [Pricing y unit economics](docs/12-pricing-y-unit-economics.md) | Tarifa, COGS, márgenes, expansión |
| 13 | [Roadmap 90 días](docs/13-roadmap-90-dias.md) | 3 fases con criterios de salida |
| 14 | [KPIs](docs/14-kpis.md) | Leading/lagging con objetivos |
| 15 | [Riesgos y deuda](docs/15-riesgos-y-deuda.md) | Commoditización, mitigaciones |
| 16 | [Equipo y operación](docs/16-equipo-y-operacion.md) | Org, burn, forward-deployed |

## Artefactos técnicos

- `examples/programs/*.yaml` — 4 programas reales (PLG, Enterprise, Ecommerce, Servicios locales)
- `examples/schema/zolts-program.schema.json` — JSON Schema del DSL
- `examples/sql/schema.sql` — DDL del núcleo canónico
- `scripts/validate.py` — valida los programas contra el esquema (`python3 scripts/validate.py`), ejecutado en CI

## Supuestos declarados

Este plan asume: (1) equipo fundador con capacidad técnica sénior, (2) 6-9 meses de runway inicial (~€700k-1M), (3) mercado inicial Europa + LATAM con expansión US, (4) sin restricción de exclusividad con ningún proveedor de datos. Cambiar cualquiera de estos altera la fase 1 del roadmap, no la tesis.
