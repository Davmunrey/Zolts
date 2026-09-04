# 09 — Ejecución multicanal y deliverability

## Tesis

> La capacidad de envío reputada es el recurso escaso del GTM moderno. Con agentes IA multiplicando el volumen de outbound, la restricción deja de ser "cuántos leads tengo" y pasa a ser "cuántos mensajes puedo enviar sin quemar mi dominio". Zolts modela la reputación como **inventario gestionado**, igual que un sistema de ads gestiona presupuesto.

## Modelo de capacidad de envío

```
capacidad_diaria = Σ_dominios Σ_buzones ( cap_base × factor_warmup × factor_reputación )

factor_warmup    ∈ [0.1, 1.0] según curva de calentamiento (semanas 1-6)
factor_reputación ∈ [0, 1.2]  función de bounce, quejas, engagement, Postmaster
```

El planificador asigna cada envío a un buzón concreto optimizando: reputación del buzón, afinidad geográfica y de idioma con el destinatario, proveedor MX del destinatario (segregar tráfico a Google vs Microsoft), y equilibrio de carga. Un buzón que degrada su reputación se retira automáticamente a warmup.

## Umbrales operativos (no negociables)

| Métrica | Objetivo | Alarma | Corte automático |
|---|---|---|---|
| Tasa de rebote | <1.5% | 2% | 3% → pausa del programa |
| Quejas de spam | <0.05% | 0.1% | 0.3% → pausa del dominio (umbral duro de los grandes proveedores) |
| Tasa de respuesta | >4% | <2% | <1% → revisión obligatoria de segmento y copy |
| Unsubscribe | <0.5% | 1% | 2% → revisión |
| Emails por buzón/día | 30-40 | 50 | 60 |
| Nuevos dominios simultáneos | — | — | Rampa escalonada, nunca activación masiva |

## Requisitos técnicos de dominio (checklist automatizado)

- SPF, DKIM (2048 bits), **DMARC con política mínima `p=quarantine`** y reportes agregados monitorizados.
- `List-Unsubscribe` + `List-Unsubscribe-Post` (one-click): obligatorio para remitentes en volumen desde 2024; su ausencia es motivo de filtrado directo.
- Dominios de envío separados del dominio corporativo principal (`get.marca.com`, `marca-hq.com`), nunca el dominio de facturación.
- Texto plano prioritario, sin imágenes de tracking en el primer toque, sin acortadores públicos, enlaces con dominio propio.
- Monitorización de listas negras y de Google Postmaster Tools vía API, con alerta antes de que caiga la tasa de entrega.
- Verificación previa a cada envío (el dato de hace 90 días ya no vale).

## Canales y sus reglas propias

| Canal | Límite práctico | Riesgo específico | Control en Zolts |
|---|---|---|---|
| Email | Ver arriba | Reputación de dominio | Planificador de capacidad + circuit breakers |
| LinkedIn | ~20-25 invitaciones/día/cuenta | Restricción o bloqueo de cuenta | Cuotas duras, jitter humano, sin automatización de navegador no soportada |
| Voz | Horario legal por país | Listas de exclusión (Robinson/DNC) | Verificación obligatoria previa a la tarea |
| WhatsApp | Plantillas aprobadas | Consentimiento obligatorio | Solo con base `consent` verificable |
| Ads | Presupuesto | Audiencias mínimas, matching de PII | Hash local, tamaños mínimos, exclusión de clientes |
| Producto (in-app) | — | Fatiga de usuario | Frecuencia global compartida con email |
| Tarea a humano | Capacidad del equipo | Cola infinita = cero ejecución | Capacidad por tier y SLA con caducidad de tarea |

## Coordinación cross-canal

Un contacto es una persona, no un registro por canal. El **frequency cap es global**: si recibió un email y una invitación de LinkedIn esta semana, el tercer toque se retrasa aunque venga de otro programa. Motor de supresión unificado: opt-outs, clientes actuales, oportunidades abiertas, cuentas de competidores, listas de exclusión legal, y "no contactar" por owner.

## Bucle de reputación → decisión

Los eventos de entrega alimentan el scoring: una cuenta cuyo dominio rebota sistemáticamente baja su `reachability`, lo que la desplaza de tier de email a tier de ads o llamada. **La deliverability no es un problema de infraestructura aislado: es una entrada del motor de decisión.** Esta integración es difícil de replicar para quien tenga la ejecución en una herramienta separada.
