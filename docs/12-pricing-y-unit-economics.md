# 12 — Pricing y unit economics

## Estructura: tarifa en tres partes

| Componente | Qué captura | Por qué |
|---|---|---|
| **Plataforma** (mensual) | Acceso al runtime, blueprints, políticas, medición | Ingreso predecible, margen ~92% |
| **Asientos** de operador | Uso humano (editores/operadores; visores gratis) | Escala con el equipo, no penaliza la visibilidad |
| **Créditos** (uso) | Enriquecimiento, IA, envíos, ejecuciones de agente | Alinea precio con valor y cubre COGS variable |

Rechazado como modelo primario: **precio por reunión generada**. Genera disputas de atribución, selección adversa (los clientes con peor producto consumen más) y desalinea el incentivo hacia el volumen. Se ofrece como add-on de rendimiento sobre lift verificado contra holdout, solo en Enterprise.

## Planes

| Plan | Plataforma/mes | Asientos incl. | Créditos incl./mes | Perfil |
|---|---|---|---|---|
| **Starter** | 490 € | 2 | 15.000 | 1-2 programas, un motion, sin SSO |
| **Growth** | 1.490 € | 5 | 60.000 | Multi-programa, todos los canales, blueprints completos |
| **Scale** | 3.900 € | 12 | 200.000 | SLA de time-to-touch, SSO, residencia UE, sandbox |
| **Enterprise** | desde 8.000 € | Custom | Custom | Aislamiento, DPA a medida, policy packs propios, soporte de GTM Engineer |

Asiento adicional: 90 €/mes. Crédito adicional: escalado por volumen (ver abajo). Servicios de implementación: 5-15 k€ únicos, **con techo del 20% del ingreso total** (por encima, el múltiplo se desploma).

## Créditos: definición y COGS

1 crédito ≈ 0,01 €. Precio decreciente por volumen: 0-100k → 0,010 €; 100k-500k → 0,008 €; >500k → 0,006 €.

| Acción | Créditos | COGS estimado | Margen bruto |
|---|---|---|---|
| Enriquecimiento de email verificado | 8 | 0,025-0,04 € | ~55% |
| Teléfono móvil | 25 | 0,10-0,15 € | ~45% |
| Firmográficos de cuenta | 4 | 0,01-0,02 € | ~60% |
| Comprobación de señal (por cuenta/día) | 0,5 | 0,001-0,004 € | ~50% |
| Generación de mensaje (IA) | 3 | 0,004-0,012 € | ~70% |
| Dossier de investigación (agente) | 20 | 0,05-0,09 € | ~65% |
| Envío de email | 1 | 0,001-0,003 € | ~80% |
| Ejecución de programa (paso) | 0,2 | ~0,0005 € | ~85% |

**Margen bruto objetivo:** 60-65% en año 1 (créditos dominan), 75-80% en año 3 (mezcla desplazada a plataforma + tarifas mayoristas de datos + infra de envío propia).

Palanca clave: el optimizador del waterfall ([07](07-motor-de-datos-waterfall.md)) mejora el margen de créditos **sin subir el precio**. Cada punto de eficiencia va directo a margen o a competitividad, a elección.

## Unit economics objetivo

| Métrica | Año 1 | Año 2 | Año 3 |
|---|---|---|---|
| ACV medio | 14 k€ | 26 k€ | 42 k€ |
| Margen bruto | 62% | 71% | 78% |
| CAC (blended) | 9 k€ | 16 k€ | 24 k€ |
| CAC payback | 12 m | 10 m | 8 m |
| NRR | 105% | 118% | 130% |
| Churn logo bruto | 22% | 15% | 10% |
| LTV/CAC | 2,1× | 3,4× | 4,8× |

Motor de expansión (por orden de contribución esperada a NRR): consumo de créditos → asientos → programas adicionales → nuevos blueprints (nuevas unidades de negocio del mismo cliente) → módulos (agentes avanzados, residencia, aislamiento).

## Poder de precio: de dónde viene

1. **Medición de incrementalidad**: si Zolts demuestra X € de pipeline incremental, el precio se ancla en un porcentaje de X, no en el coste de la herramienta sustituida.
2. **Coste de cambio legítimo**: la lógica GTM versionada del cliente vive en Zolts. No es lock-in de datos (exportables), es acumulación de conocimiento operativo — mucho más defendible y menos resentido.
3. **Compliance**: sustituir un sistema aprobado por el DPO tiene un coste organizativo que nadie asume por un 15% de descuento.
4. **Consolidación**: cada herramienta eliminada del stack (5-8 típicamente, entre 3-9 k€/mes) es presupuesto liberado que justifica el precio.

## Guardarraíl de descuento

Descuento máximo por AE: 10%. 10-20% requiere director. >20% solo por compromiso plurianual con pago anticipado. Regla dura: **jamás descontar la plataforma; descontar créditos** (elástico y con COGS variable) preserva el ancla de precio.
