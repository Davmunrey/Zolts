# 03 — Modelo de datos canónico

## Entidades núcleo

| Entidad | Descripción | Clave de resolución |
|---|---|---|
| `account` | Organización (empresa, cuenta, ubicación local) | dominio raíz + registro legal + fuzzy nombre/geo |
| `person` | Individuo en contexto profesional | email verificado > LinkedIn URN > (nombre + dominio) |
| `membership` | Relación person↔account con rol y vigencia | histórico: soporta job changes |
| `signal` | Evento observado con entidad, tipo, fuerza, fuente, timestamp | inmutable, append-only |
| `segment` | Población calculada (SQL o DSL) con versión | materializada con TTL |
| `program` | Definición versionada de una jugada GTM | semver + hash de contenido |
| `enrollment` | Instancia de un account/person en un programa | idempotente por (program_version, entity) |
| `touch` | Acción ejecutada hacia una entidad por un canal | idempotency_key único |
| `outcome` | Resultado atribuible (reply, meeting, opp, won, churn) | ligado a enrollment y a variante experimental |
| `experiment` | Holdout/variante con asignación determinista | hash estable |
| `policy_decision` | Registro de evaluación de política (allow/deny + motivo) | append-only, retención 24m |
| `cost_event` | Coste imputado (crédito, token, envío, proveedor) | base del P&L por jugada |

## Identity graph

Resolución en tres niveles, con puntuación de confianza y reglas de supervivencia (survivorship):

1. **Determinista:** email normalizado, dominio corporativo, LinkedIn URN, ID fiscal (CIF/VAT/EIN), CRM ID.
2. **Probabilística:** similitud de nombre + geo + sector + tamaño; umbral configurable por tenant (más estricto = menos falsos positivos = más coste de enriquecimiento).
3. **Humana:** cola de revisión para colisiones por encima del umbral de impacto (p. ej. cuentas con oportunidad abierta).

Reglas de supervivencia por campo: `precedencia = [CRM del cliente, proveedor con mayor accuracy histórica, dato más reciente]`. Cada valor conserva `source`, `observed_at`, `confidence` → cualquier dato es explicable y revocable (requisito GDPR de exactitud).

**Nunca fusionar destructivamente.** Los merges son reversibles: se materializa un `golden_record` derivado, los registros fuente permanecen.

## Multi-tenancy y extensibilidad

- Aislamiento por `tenant_id` + Row Level Security en todas las tablas.
- Campos custom en `attributes JSONB` validados contra un **esquema declarado por tenant** (`tenant_schema`), versionado. Esto es lo que permite servir a un fabricante industrial y a un SaaS PLG con el mismo core.
- Todos los objetos de configuración (segmentos, programas, señales, políticas) heredan por overlay: `blueprint → industry_pack → tenant → program`. Ver [05](05-blueprints-adaptabilidad.md).

## Retención y minimización

| Clase de dato | Retención por defecto | Nota |
|---|---|---|
| PII de contactos no convertidos | 12 meses desde último contacto | Configurable por jurisdicción |
| Señales brutas | 13 meses | Se conservan agregados sin PII indefinidamente |
| Traces de ejecución | 24 meses | Requisito de auditoría |
| Contenido generado por IA | 24 meses | Trazabilidad AI Act |
| Vectores de embeddings de PII | Ligados al TTL del origen | Borrado en cascada |

DSAR (acceso/supresión) automatizado: una petición resuelve por identity graph y ejecuta borrado en cascada + propagación a subencargados, con certificado de ejecución.

## DDL de referencia

Ver [`examples/sql/schema.sql`](../examples/sql/schema.sql).
