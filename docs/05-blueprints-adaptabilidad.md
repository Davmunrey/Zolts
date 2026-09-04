# 05 — Blueprints: cómo Zolts se adapta a cualquier tipo de empresa

## El problema que resuelve

"Adaptable a cualquier empresa" se implementa mal de dos formas: (a) producto genérico vacío que exige 6 semanas de consultoría, o (b) fork por cliente que destruye el margen. Zolts usa una tercera vía: **herencia por overlays** con extracción de plantillas.

## Cadena de herencia

```
Blueprint (arquetipo)  →  Industry Pack  →  Tenant  →  Program
      global                sectorial       cliente     jugada
```

Cada nivel puede **añadir** o **restringir**, nunca relajar política. La resolución es determinista y el resultado efectivo se puede inspeccionar (`zolts explain program X --resolved`). Es el modelo de kustomize aplicado a GTM: las mejoras del blueprint fluyen a todos los clientes sin romper sus personalizaciones.

## Perfilado: 12 dimensiones

El onboarding resuelve el blueprint a partir de un perfil, no de una entrevista libre:

| # | Dimensión | Valores | Qué determina |
|---|---|---|---|
| 1 | Motion | PLG / SLG / PLS / channel / self-serve | Señales primarias y tiers |
| 2 | ACV | <1k / 1-10k / 10-50k / 50-250k / >250k | Coste máximo por cuenta, ratio humano/agente |
| 3 | Ciclo de venta | <7d / 1-3m / 3-9m / >9m | Ventanas de medición y decay |
| 4 | Tipo de cliente | B2B / B2C / B2B2C / público | Base legal dominante |
| 5 | Amplitud de ICP | nicho (<5k cuentas) / medio / masivo | Enriquecimiento agresivo vs. selectivo |
| 6 | Geografía | UE / UK / US / LATAM / APAC | Policy pack, horarios, canales legales |
| 7 | Madurez de datos | sin warehouse / warehouse / lakehouse | Modo de ingesta (copia vs. zero-copy) |
| 8 | CRM | HubSpot / Salesforce / Attio / Pipedrive / ninguno | Mapper y writeback |
| 9 | Equipo comercial | 0 / 1-5 / 5-25 / >25 | Capacidad por tier, routing territorial |
| 10 | Producto | software / servicios / físico / marketplace | Señales de producto disponibles |
| 11 | Compliance | estándar / regulado / sector público | Aprobaciones, retención, residencia |
| 12 | Canales permitidos | email / LinkedIn / voz / WhatsApp / ads | Play templates disponibles |

El resolver es una **tabla de decisión determinista** (auditable) con refinamiento asistido por LLM solo para el copy y la priorización inicial — nunca para la política.

## Los 10 arquetipos de salida

| Blueprint | Señal primaria | Jugada dominante | KPI norte | Compliance |
|---|---|---|---|---|
| `b2b-saas-plg` | Uso de producto (límites, multi-usuario) | PLS interceptación en el momento de fricción | Free→paid 30d | Contrato (usuarios propios) |
| `b2b-saas-sales-led` | Funding + hiring + job change | Multihilo 1:1 sobre buying center | Opps creadas 90d | Interés legítimo |
| `b2b-enterprise` | Intent de categoría + comité + evento | ABM orquestada, ads + exec touch | Pipeline por cuenta objetivo | Alto (DPA, residencia) |
| `services-agency` | Publicaciones de empleo, RFP, cambios de agencia | Outbound consultivo + prueba de trabajo | Reuniones cualificadas | Interés legítimo |
| `fintech-regulated` | Licencias, funding, cambios normativos | Contenido + venta consultiva con aprobación | Opps cualificadas | Muy alto (aprobación de copy) |
| `healthtech-lifesci` | Tenders, ensayos, contrataciones clínicas | Cuenta larga, muchos stakeholders | Cuentas activadas | Muy alto (datos sensibles) |
| `ecommerce-dtc` | Comportamiento de compra, carrito, RFM | Winback / replenishment / cross-sell | Ingreso neto 28d | Consentimiento (B2C) |
| `marketplace-2sided` | Desequilibrio oferta/demanda por geo | Captación del lado escaso por territorio | Liquidez por geo | Mixto |
| `industrial-b2b` | Licitaciones, expansión de planta, importaciones | Distribuidor + venta técnica larga | Cotizaciones emitidas | Estándar |
| `local-services-multisite` | Nueva sede, reseñas, actividad publicitaria | Teléfono + WhatsApp por territorio | Contratos firmados 60d | Listas de exclusión |

Cada blueprint entrega, de fábrica: 3-6 programas, 8-15 señales configuradas, un modelo de scoring precalibrado, un policy pack, un dashboard y un set de tests declarativos.

## Extensión sin fork

| Necesidad del cliente | Mecanismo | Sin tocar código |
|---|---|---|
| Objeto de negocio propio (p. ej. "Póliza", "Obra") | `tenant_schema` + `attributes JSONB` | Sí |
| Fuente de datos propietaria | Conector genérico (REST/SQL/CSV/webhook) via SDK | Sí |
| Señal propia | `SignalDefinition` declarativa | Sí |
| Regla de scoring del sector | `formula` o `ScoreModel` entrenado con su historial | Sí |
| Canal exótico (Telegram, portal propio) | Adaptador de canal (SDK, ~200 líneas) | Requiere adaptador |
| Regulación específica | `Policy` pack heredable | Sí |

## Bucle de mejora: extracción de plantillas

Cuando un cliente crea un programa que supera al benchmark (lift verificado contra holdout), el sistema propone **anonimizarlo y promoverlo** al blueprint como plantilla. Efecto: el producto mejora con cada cliente sin que ningún dato cruce fronteras de tenant. Es el mecanismo que convierte servicios en producto y evita la trampa de consultoría.

Reglas: promoción solo con consentimiento explícito del tenant, eliminación de todo literal identificable, y validación en ≥3 tenants antes de pasar a `stable`.
