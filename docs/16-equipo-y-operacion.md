# 16 — Equipo, coste y operación

## Equipo mínimo viable (90 días)

| Rol | FTE | Responsabilidad | Coste/mes (Europa, cargado) |
|---|---|---|---|
| Founding engineer — backend/runtime | 1,0 | Temporal, DSL, idempotencia, policy engine | 9-11 k€ |
| Ingeniero de datos/plataforma | 1,0 | Ingesta, identity graph, waterfall router, warehouse | 8-10 k€ |
| Ingeniero full-stack | 1,0 | Studio, CLI, onboarding, facturación | 7-9 k€ |
| Ingeniero de IA/ML | 0,5-1,0 | Agentes, evals, scoring | 9-12 k€ |
| **Forward-Deployed GTM Engineer** | 1,0 | Implanta design partners y **convierte cada implantación en blueprint** | 6-8 k€ |
| Fundador/CEO | 1,0 | Venta, design partners, producto | — |
| Diseño (fraccional) | 0,3 | Studio y dashboards | 3 k€ |
| Legal/DPO (fraccional) | 0,2 | Packs de política, DPAs | 3-4 k€ |

**Burn:** 48-62 k€/mes. **Trimestre:** ~150-185 k€ + 15-25 k€ de infra, datos y herramientas. Con 15 k€ de pilotos cobrados, quema neta del trimestre ≈ 155-195 k€.

El rol crítico y menos obvio es el **Forward-Deployed GTM Engineer**: es simultáneamente el motor de éxito de los primeros clientes y el mecanismo que impide la trampa de servicios, porque su entregable no es "el cliente contento" sino "el blueprint que hace que el siguiente cliente no me necesite".

## Ritmo operativo

| Cadencia | Foro | Decisión que produce |
|---|---|---|
| Diaria | Standup de 15 min | Desbloqueo |
| Semanal | Revisión de métricas de producto (latencia, coste/contacto, evals, deliverability) | Ajuste de sprint |
| Semanal | Revisión de pipeline y de cada design partner | Riesgo comercial |
| Quincenal | Revisión de lift de programas de clientes | Qué se promueve a blueprint |
| Mensual | Revisión de arquitectura + ADRs + deuda | Qué deuda se paga |
| Trimestral | Criterios de salida de fase | Avanzar / recortar alcance |

## Contratación posterior (meses 4-12, por orden)

1. AE #1 (mes 7) — solo con playbook validado por el fundador.
2. Ingeniero de conectores (mes 5) — cuando el mantenimiento supere el 20% del tiempo de ingeniería.
3. Ingeniero de deliverability/infra de envío (mes 8) — cuando se internalice el envío.
4. Data scientist (mes 9) — cuando haya volumen suficiente para modelos por tenant.
5. Segundo Forward-Deployed GTM Engineer (mes 6) — el cuello de botella de implantación llega antes que el de ventas.

## Principios de ingeniería

1. **Nada llega a producción sin trace, coste imputado y decisión de política registrada.**
2. **Los conectores pasan tests de contrato o no se mergean.**
3. **Ningún cambio de prompt o de modelo sin pasar el golden set en CI.**
4. **Todo lo que un cliente pida "a medida" se implementa como configuración o no se implementa.**
5. **Si no se puede medir el lift, no se lanza el programa.**
