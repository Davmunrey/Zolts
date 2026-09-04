# 17 — Secuencia de comprador: operador y CFO

## La pregunta

"¿Por qué no ambos?" — operador (RevOps/GTM Engineer) y CFO simultáneamente.

## La respuesta corta

**Ambos sí como buying center. No como motion simultánea.** No son dos segmentos: son dos roles del mismo comité, con distinto momento de entrada. Lo que no se puede duplicar en fase temprana no es la narrativa — es la **superficie de producto, el ancla de precio y el ciclo de caja**.

## Qué comparten (coste marginal ≈ 0, se construye una vez)

| Componente | Sirve al operador como… | Sirve al CFO como… |
|---|---|---|
| Motor de holdouts | Prueba de que su jugada funciona | Control de gasto justificado |
| P&L por jugada | Diagnóstico de qué recortar | Estado de resultados de GTM |
| Policy engine | Evita quemar dominios | Reduce riesgo regulatorio |
| Ledger de costes | Presupuesto de programa | Spend under management |

El 70% del producto es indiferente al comprador. Ahí "ambos" es gratis y hay que hacerlo.

## Qué se bifurca (coste real, obliga a elegir)

| Dimensión | Operador | CFO | ¿Se puede hacer a la vez con 6-8 personas? |
|---|---|---|---|
| **Primera experiencia** | Builder: programa vivo en <60 min | Diagnóstico: auditoría de gasto + business case | **No** — son dos productos de primer uso |
| **Ancla de precio** | 490-1.490 €/mes + créditos, self-serve | 8-40 k€/año sobre % del gasto GTM gobernado | **No** — coexistir destruye el ancla alta |
| **Time-to-value** | 7 días | 1 ciclo de venta completo (60-120 días) | **No** — ciclos de caja incompatibles |
| **Prueba requerida** | Un programa que funciona | Baseline medido + lift significativo | **No** — la segunda depende de la primera |
| **Roadmap** | Studio, CLI, blueprints, velocidad | Procurement, SSO, SOC 2, residencia, reporting consolidado | **No** en 90 días |
| **Contratación** | Growth engineer / self-serve | AE con venta a nivel C | **No** antes del mes 7 |

## La secuencia forzada (no es preferencia, es física)

La venta al CFO exige tres activos que **solo existen ejecutando**: (1) baseline del gasto GTM actual, (2) lift medido contra holdout sobre ≥1 ciclo, (3) coste real por reunión y por euro de pipeline. Vender al CFO primero significa vender un business case sin datos — precisamente lo que hace todo competidor y por lo que nadie le cree.

**Acto 1 (meses 0-12) — Land con el operador.** Créditos, velocidad, adopción. Objetivo real: acumular ejecución, que es la materia prima del moat.

**Acto 2 (meses 9-24) — Expand hacia el CFO.** No es un segmento nuevo: es un upsell dentro de una cuenta existente, con la prueba ya acumulada. Conversión de contrato de consumo a contrato de plataforma con gobierno de gasto, a 3-5× el ACV.

Los actos se solapan tres meses a propósito: el mes 9 se prueba el pitch de CFO en clientes que ya tienen 6 meses de datos.

## El requisito irreversible de la fase 1

> **Capturar el baseline y el ledger de gasto desde el día 1 de cada tenant, aunque nadie venda sobre ello hasta el mes 9.**

Razón: un baseline no se reconstruye retroactivamente. Si en el mes 9 no existe el "antes", el Acto 2 se cae o exige reinstrumentar y esperar otro ciclo completo. Coste de hacerlo ahora: ~10-15% de ingeniería en fases 1-2. Coste de no hacerlo: 6-9 meses de retraso en el Acto 2.

Se capturan desde el primer onboarding, sin fricción para el usuario:

- Gasto actual por herramienta y por canal (declarado en onboarding, 4 campos).
- Volumen y tasas de conversión de los 90 días previos (importados del CRM).
- Coste por reunión y por oportunidad pre-Zolts.
- Snapshot congelado y firmado: es el documento del Acto 2.

## La trampa del comprador dual (y su guardarraíl)

Riesgo: construir un builder demasiado técnico para el CFO y una capa de gobierno demasiado pesada para el operador → producto mediocre para ambos.

**Guardarraíl duro:** la superficie del CFO debe ser **derivada** de los datos que el operador ya genera, con **cero input adicional del cliente**. En cuanto la capa de CFO exija que alguien introduzca datos o configure algo propio, ha dejado de ser una vista y se ha convertido en un segundo producto. Ese es el punto exacto donde "ambos" pasa de ser apalancamiento a ser dispersión.

## Prohibiciones explícitas en los primeros 90 días

1. Sin segunda página de precios ni tarifa "Finance".
2. Sin AE enterprise antes del mes 7.
3. Sin módulo de gobierno de gasto que requiera configuración propia.
4. Sin pilotos vendidos al CFO como comprador principal (puede ser aprobador, no comprador).
