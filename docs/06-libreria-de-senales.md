# 06 — Librería de señales

## Tesis operativa

> El mensaje importa menos que el momento. **El time-to-touch es la variable de mayor apalancamiento de todo el GTM**: la misma jugada ejecutada en <1h vs >72h tras la señal cambia la tasa de respuesta en un factor de 2-4×. Zolts trata la latencia señal→acción como un SLA de producto, no como un detalle.

## Anatomía de una señal

```yaml
apiVersion: zolts/v1
kind: SignalDefinition
metadata: {key: hiring.role_opened}
spec:
  entity: account
  source: {connector: jobs_feed, refresh: 6h}
  freshness_sla: 12h          # antigüedad máxima aceptable del dato
  half_life_h: 720            # decay: a los 30d vale la mitad
  base_strength: 0.6
  legal_basis: legitimate_interest
  cost_per_check_eur: 0.004
  dedupe_window: 14d
  payload_schema: {department: string, count: int, seniority: string, jd_keywords: [string]}
```

Cada señal declara **coste**, **decay** y **base legal**. Sin las tres, no se puede ni presupuestar ni auditar.

## Catálogo (v1)

### Tier A — alta intención, decay rápido (SLA de acción <4h)

| Señal | Fuente | Vida media | Uso |
|---|---|---|---|
| `web.pricing_page_visit` | Deanonimización web (RB2B/Vector/propia) | 48h | El visitante de pricing es el lead más caliente que existe |
| `web.docs_or_api_visit` | Analytics propio | 72h | Señal técnica: entra el evaluador, no el comprador |
| `product.limit_hit` | Evento de producto | 96h | PLS: fricción = presupuesto desbloqueado |
| `inbound.form_submitted` | Formularios | 1h | Speed-to-lead: cada 10 min de retraso cuesta conversión |
| `review.competitor_page_view` | G2/Capterra intent | 96h | Comparación activa = ciclo abierto |
| `event.booth_scan` / `webinar_attended` | Eventos | 120h | Ventana corta post-evento |

### Tier B — cambio estructural, decay medio (SLA <48h)

| Señal | Fuente | Vida media | Uso |
|---|---|---|---|
| `funding.round` | Crunchbase/Harmonic/prensa | 30d | Presupuesto nuevo + presión de crecimiento |
| `people.job_change` | LinkedIn/UserGems | 45d | Un campeón que cambia de empresa es el mejor lead del mundo |
| `people.exec_hired` | LinkedIn/prensa | 60d | Nuevo directivo = 90 días para dejar huella = compra |
| `hiring.role_opened` | Feeds de empleo | 30d | La oferta de empleo es la especificación pública de un dolor |
| `tech.install_detected` / `tech.uninstall` | BuiltWith/HG/DNS | 45d | Complemento o desplazamiento de competidor |
| `corp.m_and_a` | Prensa/registro | 60d | Consolidación de stack: ventana de reemplazo |
| `local.new_location_detected` | Google Business/registros | 60d | Expansión operativa |

### Tier C — contexto y ajuste, decay lento (alimenta fit, no timing)

| Señal | Fuente | Uso |
|---|---|---|
| `firmo.headcount_growth_by_dept` | LinkedIn/proveedores | Tendencia de inversión por función |
| `intent.topic_surge` | Bombora/6sense | Intent de categoría a nivel cuenta |
| `content.engagement` | Social/newsletter | Identifica campeones latentes |
| `public.tender_published` | Boletines oficiales (BOE/TED) | Sector público e industrial |
| `esg.report_published` | Registros públicos | Compliance-driven selling |
| `commerce.app_installed` | Shopify/marketplaces | Ecommerce: stack y madurez |
| `partner.shared_customer` | Datos de partner | El canal con mayor tasa de cierre |

### Tier D — riesgo y expansión (post-venta)

| Señal | Fuente | Uso |
|---|---|---|
| `cs.champion_left` | CRM/LinkedIn | Predictor #1 de churn |
| `cs.usage_decline` | Producto | Churn temprano |
| `cs.renewal_window` | CRM | Timing de renovación |
| `cs.expansion_headroom` | Producto | Upsell cuantificado |

## Composición: la señal compuesta gana

Una señal aislada tiene poca precisión. El valor está en la **conjunción con ventana**:

```
funding.round (30d) ∧ hiring.role_opened[revops] (30d) ∧ tech.uninstall[competidor] (45d)
→ probabilidad de oportunidad ~6-9× baseline
```

Zolts modela esto como `combine: all_within` en el trigger y penaliza la sobre-conjunción (poblaciones de 3 cuentas no son un programa, son una tarea).

## Función de decay

```
strength_t = base_strength × 0.5^(Δt / half_life_h) × confidence_source
intent_score = 1 - Π(1 - strength_t,i)     # combinación probabilística, no suma
```

La combinación probabilística evita el error clásico de sumar señales y saturar el score con ruido correlacionado.

## SLA de time-to-touch (compromiso de producto)

| Tier de señal | Ingesta → señal disponible | Señal → acción propuesta | Señal → acción ejecutada |
|---|---|---|---|
| A | p95 < 5 min | p95 < 10 min | p95 < 60 min |
| B | p95 < 60 min | p95 < 2h | p95 < 24h |
| C | p95 < 24h | batch diario | batch diario |
| D | p95 < 6h | p95 < 12h | según playbook CS |

Estos números son KPIs de ingeniería, no aspiraciones de marketing: aparecen en el dashboard del cliente y en el SLA contractual de los planes Scale/Enterprise.

## Higiene de señales

- **Supresión de ruido:** una señal cuyo lift medido contra holdout no supera el baseline en 90 días se degrada automáticamente a `advisory` (no dispara programas) y se avisa al owner. El catálogo se poda solo.
- **Coste por señal en el P&L:** cada señal reporta coste/mes y contribución a pipeline. Las señales de pago que no pagan su coste se desactivan.
- **Anti-doble-toque:** deduplicación cross-programa a nivel persona; una cuenta no puede recibir tres programas simultáneos aunque cumpla los tres triggers (prioridad por score y por owner).
