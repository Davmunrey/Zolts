# 07 — Motor de datos: waterfall router y economía del enriquecimiento

## Principio

El usuario **nunca elige proveedor**. Declara el resultado que necesita:

```yaml
enrich:
  person:
    require: [work_email, phone_mobile]
    accuracy_sla: 0.95
    max_cost_per_contact: 0.60
```

El router resuelve qué proveedores consultar, en qué orden y cuándo parar. Esto es lo que convierte a Zolts en la capa de arbitraje del mercado de datos B2B.

## Optimizador de coste esperado

Para un campo `f` con proveedores ordenables `p1..pn`, cada uno con tasa de acierto condicional `h_i` (dado que los anteriores fallaron), coste `c_i` y precisión `a_i`:

```
E[coste] = Σ_i  c_i × Π_{j<i} (1 - h_j)
E[cobertura] = 1 - Π_i (1 - h_i)
```

El router resuelve un orden que **minimiza E[coste] sujeto a E[cobertura] ≥ objetivo y precisión ponderada ≥ accuracy_sla**. Como `h_i` depende del segmento (país, tamaño, sector, seniority), se mantiene una matriz de tasas de acierto por *cohorte*, actualizada con cada llamada.

**Consecuencia económica:** en cohortes donde un proveedor barato acierta el 70%, se ahorra ~40-60% frente al waterfall estático que la mayoría de equipos configura a mano. Ese ahorro es simultáneamente propuesta de valor y margen bruto.

## Capas de ahorro (por orden de impacto)

| # | Mecanismo | Ahorro típico | Nota |
|---|---|---|---|
| 1 | **No enriquecer lo que no se va a usar** | 30-50% | Enriquecer *después* del scoring de fit, nunca antes. El error más caro del sector. |
| 2 | Caché por tenant con TTL por campo | 15-25% | Email 180d, teléfono 365d, firmográficos 90d, tecnografía 45d |
| 3 | Matriz de acierto por cohorte | 20-40% | Ordena el waterfall dinámicamente |
| 4 | Parada temprana por SLA | 10-15% | No consultar el 4º proveedor si ya se cumple la precisión requerida |
| 5 | Estadística global anonimizada | 5-15% | Tasas de acierto agregadas entre tenants; **nunca valores de datos** |
| 6 | Negociación mayorista | 20-40% en COGS | Consecuencia de agregar volumen: se activa a partir de ~50 clientes |

La capa 5 es delicada: se comparten *métricas de rendimiento de proveedores*, jamás registros. La frontera está codificada en el motor de políticas y auditada.

## Contrato de proveedor

```yaml
kind: Connector
metadata: {key: provider_x}
spec:
  fields: [work_email, phone_mobile, linkedin_urn]
  limits: {rps: 10, daily: 50000, burst: 50}
  pricing: {model: per_hit, unit_cost_eur: 0.021, minimum_commit_eur: 500}
  quality: {measured: true}       # accuracy verificada, no declarada por el vendor
  legal: {dpa: signed, subprocessor: true, regions: [eu, us], basis_supported: [legitimate_interest]}
  failure_policy: {timeout_ms: 4000, retries: 2, circuit_breaker: 5xx_rate>0.2/60s}
```

**La precisión se mide, no se acepta.** Cada email enviado devuelve verdad-terreno (bounce, respuesta, verificación); esa señal reentrena la matriz de acierto. A los 6 meses, Zolts conoce la calidad real de cada proveedor por cohorte mejor que el propio proveedor. Ese dataset es un activo defendible.

## Verificación y calidad

- Verificación de email en cascada: sintaxis → MX → SMTP (donde sea aceptable) → proveedor de verificación → señal histórica de bounce.
- **Catch-all**: no se descartan, se marcan `risky` y se enrutan a buzones de menor reputación o a otro canal. Descartarlos elimina el 20-30% del TAM alcanzable en Europa.
- Roles genéricos (`info@`, `sales@`) excluidos por defecto de secuencias 1:1.
- Presupuesto de riesgo: cada programa declara su tolerancia a bounce; el router respeta ese presupuesto.

## Salvaguardas

| Riesgo | Control |
|---|---|
| Fuga de gasto | Techos por programa/tenant/día; `on_exceed: pause_and_alert` |
| Proveedor caído | Circuit breaker + reordenación automática del waterfall |
| Cambio de precio del proveedor | Precios como configuración, no código; recálculo del optimizador en caliente |
| Dependencia única | Mínimo 2 proveedores por campo crítico antes de considerarlo GA |
| Dato ilícito | Cada proveedor declara base legal y regiones; el policy engine bloquea combinaciones inválidas antes de la llamada |
