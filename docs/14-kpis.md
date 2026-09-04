# 14 — KPIs

## Producto y ejecución

| Métrica | Tipo | Objetivo 90d | Objetivo 12m |
|---|---|---|---|
| Time-to-first-program (nuevo tenant) | Leading | <60 min | <20 min |
| Latencia p95 señal→acción ejecutada (tier A) | Leading | <60 min | <15 min |
| Programas activos por tenant | Leading | 3 | 8 |
| % de acciones con decisión de política registrada | Leading | 100% | 100% |
| Coste por contacto verificado | Leading | −30% vs. waterfall estático | −50% |
| Tasa de rebote de email | Leading | <2% | <1,2% |
| Tasa de quejas de spam | Leading | <0,1% | <0,05% |
| Tasa de respuesta positiva | Leading | >3% | >6% |
| % de afirmaciones con procedencia verificada | Leading | >90% | >98% |
| % de toques tier 2/3 auto-enviados | Leading | 60% | 85% |
| Incidencias de marca por contenido IA | Lagging | 0 | 0 |
| Lift incremental medio vs. holdout | Lagging | >1,5× | >2,5× |
| Uptime del runtime | Lagging | 99,5% | 99,9% |

## Negocio

| Métrica | Tipo | Objetivo 90d | Objetivo 12m |
|---|---|---|---|
| Design partners de pago | Leading | 3 | — |
| Clientes de pago | Lagging | 10 | 45 |
| ARR | Lagging | 180-250 k€ | 1,1-1,5 M€ |
| ACV medio | Lagging | 14 k€ | 20 k€ |
| Conversión piloto→anual | Leading | >66% | >75% |
| Margen bruto | Lagging | >55% | >68% |
| NRR | Lagging | — | >115% |
| CAC payback | Lagging | — | <12 m |
| Días de implantación (mediana) | Leading | <5 | <2 |
| % de tenants con baseline capturado en 7 días | Leading | 100% | 100% |
| Cuentas con lift significativo acumulado (elegibles Acto 2) | Leading | — | >60% |
| % de ingresos de servicios | Leading | <25% | <15% |
| Ratio ingeniería dedicada a un solo cliente | Leading | <20% | <5% |

## Métrica única de salud (North Star)

**Pipeline incremental verificado por holdout y generado a través de Zolts, por mes.**

Razón: es la única métrica que no se puede inflar con actividad. Enviar más emails no la mueve; solo la mueve ejecutar la jugada correcta en el momento correcto. Alinea producto, ingeniería y ventas en el mismo objetivo y es exactamente el número que el cliente lleva a su comité.

## Contra-métricas (vigiladas para evitar optimización perversa)

| Si sube esto… | …es señal de que algo va mal |
|---|---|
| Volumen de envíos por cliente sin subir el lift | Estamos vendiendo actividad, no resultado |
| Créditos consumidos sin subir el pipeline incremental | El router o el scoring están mal calibrados |
| Ingresos de servicios | El producto no es adaptable de verdad |
| Programas creados pero nunca publicados | Fricción en `plan`/`test`; la UI no es usable |
| Tickets de soporte por conector | Deuda de conectores desbordando |
