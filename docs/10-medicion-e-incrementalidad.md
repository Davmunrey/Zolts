# 10 — Medición: incrementalidad, no atribución

## El problema

La atribución multi-touch responde "¿a quién le doy el crédito?" — una pregunta política. La pregunta económica es "¿qué habría pasado si no hago nada?". Todo el sector vende lo primero. Zolts entrega lo segundo y hace de ello su argumento ante el CFO.

## Diseño experimental por defecto

- **Todo programa declara un holdout** (mínimo 5%; por debajo requiere justificación escrita registrada).
- Asignación determinista: `variant = hash(entity_id + program_key + salt) % 100 < holdout_pct → control`. Estable ante reejecuciones y auditable.
- Unidad de aleatorización según motion: cuenta (B2B), persona (B2C), territorio o geo (cuando hay contaminación entre unidades, p. ej. servicios locales o ads).
- **Guardrails**: métricas que, si se degradan, paran el experimento automáticamente (quejas de spam, unsubscribe, margen por pedido).

## Métricas

```
lift_absoluto   = tasa_tratamiento − tasa_control
lift_relativo   = lift_absoluto / tasa_control
pipeline_incremental = lift_absoluto × N_tratamiento × valor_medio_oportunidad
ROI_programa    = (margen_incremental − coste_programa) / coste_programa
```

Potencia estadística: el sistema calcula **antes** de publicar cuántas semanas hace falta para detectar el lift mínimo relevante (MDE). Si el programa no puede alcanzar significancia en un plazo razonable, se dice explícitamente y se sugiere agrupar programas en un experimento sombrilla. Reportar un lift no significativo como éxito es el fracaso más común de los equipos GTM.

Para ciclos largos (>6 meses) se usan métricas proxy validadas contra el histórico: reunión cualificada → oportunidad → cierre, con las tasas de conversión propias del tenant, y se reporta el intervalo de confianza, nunca el punto.

## P&L por jugada

Cada programa reporta un estado de resultados real:

| Línea | Fuente |
|---|---|
| Coste de datos | `cost_event.kind = enrichment` |
| Coste de IA | `cost_event.kind = llm` |
| Coste de envío/ads | `cost_event.kind in (send, ads)` |
| Coste de tiempo humano | tareas × coste/hora del rol configurado |
| **Coste total** | suma |
| Pipeline incremental | del experimento |
| Ingreso incremental cerrado | del experimento, ventana de ciclo |
| **ROI y coste por reunión incremental** | derivados |

Este cuadro, y no el número de emails enviados, es la pantalla principal del producto. Cambia la conversación de "actividad" a "retorno" y es lo que sostiene el precio.

## Atribución (como diagnóstico, no como verdad)

Se mantiene atribución multi-touch para responder preguntas de diagnóstico (qué canal aparece en los ciclos ganados, qué secuencia precede a una respuesta), claramente etiquetada como **correlacional**. La verdad causal viene solo del experimento. Esta distinción explícita es una postura de producto y también de honestidad comercial.

## Anti-patrones bloqueados por diseño

| Anti-patrón | Bloqueo |
|---|---|
| Holdout que "se filtra" (control recibe toques de otro programa) | Registro de exposición cross-programa; contaminación reportada y excluida |
| Cambiar la métrica primaria a posteriori | Métrica congelada al publicar la versión |
| Parar el test al ver un resultado favorable | Duración mínima y corrección por peeking |
| Comparar periodos, no grupos | Solo se reportan comparaciones contra control concurrente |
