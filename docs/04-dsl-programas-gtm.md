# 04 — DSL de Programas GTM (config-as-code)

## Por qué un DSL y no una UI

| Sin DSL (estado del arte hoy) | Con DSL |
|---|---|
| Nadie sabe quién cambió el filtro que rompió la campaña | `git blame` |
| No hay entorno de staging | `zolts plan` + dry-run sobre snapshot |
| Rollback = rehacer a mano | `zolts rollback --to 1.4.2` |
| Imposible auditar ante DPO o CFO | Diff firmado + trace por ejecución |
| Cada cliente es un fork | Overlays sobre un blueprint común |

La UI (Studio) es un editor bidireccional sobre este YAML. Un GTM Engineer trabaja en el repo; un AE trabaja en Studio; ambos producen el mismo artefacto.

## Anatomía de un Program

```yaml
apiVersion: zolts/v1
kind: Program
metadata:
  key: series-a-hiring-surge
  version: 2.1.0
  owner: gtm-eng@empresa.com
  blueprint: b2b-saas-sales-led      # herencia
spec:
  trigger:                            # 1. SEÑAL
  audience:                           # 2. SEGMENTO
  enrich:                             # 3. ENRIQUECIMIENTO
  score:                              # 4. DECISIÓN
  route:                              # 5. ASIGNACIÓN A TIER
  plays:                              # 6. JUGADAS POR TIER
  policy:                             # 7. GOBIERNO
  experiment:                         # 8. MEDICIÓN (obligatorio)
  budget:                             # 9. LÍMITES ECONÓMICOS
  exit:                               # 10. CONDICIONES DE SALIDA
```

### Bloques

| Bloque | Contrato | Nota de ingeniería |
|---|---|---|
| `trigger` | `events: [signal.type]` + `window` + `dedupe` | Event-driven por defecto; `schedule` solo para reconciliación |
| `audience` | SQL sobre la capa semántica o referencia a `Segment` | Se compila y se explica (`EXPLAIN`) antes de publicar |
| `enrich` | Lista de campos requeridos + SLA de accuracy + techo de coste | El *router* decide proveedores, no el usuario |
| `score` | Fórmula PIT-R declarativa o modelo entrenado (`model_ref`) | Debe devolver contribución por factor (explicabilidad) |
| `route` | Umbrales → tier (t1/t2/t3) + capacidad por tier | La capacidad humana es un recurso finito modelado |
| `plays` | Secuencia de pasos por canal con esperas y ramificación | Cada paso emite `proposed_action`, nunca ejecuta directo |
| `policy` | Overrides sobre la política del tenant (solo más restrictivos) | Un programa **no puede** relajar la política global |
| `experiment` | `holdout_pct` obligatorio ≥ 5% (justificación requerida para 0) | Sin esto no hay `zolts apply` |
| `budget` | Créditos/mes, coste máx. por cuenta, coste máx. por reunión | Corta la ejecución automáticamente al llegar al techo |
| `exit` | Reply, reunión, opp creada, desuscripción, agotamiento | Evita el clásico "seguimos escribiendo a un cliente" |

## Ciclo de vida y testing

```
zolts lint            → validación de esquema + reglas de política estática
zolts plan            → dry-run sobre snapshot: cuántas cuentas, coste estimado, muestras de mensaje
zolts test            → tests declarativos (fixtures de cuentas → decisiones esperadas)
zolts apply --stage   → ejecución en shadow mode (todo se calcula, nada se envía)
zolts apply --live    → publica versión y arranca enrollments
zolts rollback        → vuelve a versión anterior; enrollments activos se drenan, no se cortan
```

**Tests declarativos** (el diferenciador frente a Clay/n8n):

```yaml
kind: ProgramTest
program: series-a-hiring-surge@2.1.0
cases:
  - name: cuenta UE sin base legal no recibe email
    given:
      account: {country: DE, employee_band: "51-200"}
      person:  {country: DE, consent_state: {}}
    expect:
      policy_decision: deny
      rule: eprivacy.de.b2b_email_requires_consent
  - name: señal caducada no dispara
    given:
      signal: {type: funding.round, observed_at: "-45d", half_life_h: 720}
    expect:
      enrolled: false
  - name: tier 1 va a humano, no a agente
    given: {score: 88}
    expect: {tier: t1, play: exec-1to1, auto_send: false}
```

Estos tests corren en CI. Un cambio en un programa que rompa una regla de compliance **no se puede mergear**. Ese es el argumento de venta ante un DPO enterprise.

## Recursos del DSL

`Program`, `Segment`, `SignalDefinition`, `PlayTemplate`, `MessageTemplate`, `Policy`, `Blueprint`, `Connector`, `ScoreModel`, `ProgramTest`.

Esquema formal: [`examples/schema/zolts-program.schema.json`](../examples/schema/zolts-program.schema.json).
Ejemplos completos: [`examples/programs/`](../examples/programs/).

## Notas de diseño del DSL

- **`events:` y no `on:`** — YAML 1.1 coacciona `on`/`off`/`yes`/`no` a booleanos. Usar `on:` como clave produce `True` en parsers PyYAML y rompe la validación silenciosamente (el mismo bug que arrastra GitHub Actions). Los 4 programas de ejemplo validan contra el JSON Schema con `scripts/validate.py`.
- **Sin expresiones Turing-completas.** `where` y `when` son expresiones booleanas restringidas (subconjunto tipo CEL). Un DSL ejecutable arbitrario impide el análisis estático de política y convierte el motor en un intérprete inseguro.
- **Semver con significado:** `major` = cambia la población o la base legal (requiere re-consentimiento del owner); `minor` = nuevos pasos o canales; `patch` = copy y umbrales.
- **Enrollments activos no se cortan en rollback**: se drenan con la versión con la que entraron. Cortar secuencias a medias destruye la experiencia del prospecto y falsea los experimentos.
