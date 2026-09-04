# 01 — Mercado, competencia y posicionamiento

## El problema real (diagnóstico de GTM Engineer)

El "modern GTM stack" típico de una empresa B2B de 200 empleados:

| Capa | Herramientas típicas | Fallo estructural |
|---|---|---|
| Registro | Salesforce / HubSpot / Attio | No ejecuta; el dato llega tarde y sucio |
| Datos | Apollo, ZoomInfo, Cognism, Clearbit, Crunchbase | Waterfall manual, créditos duplicados, sin caché |
| Señales | Bombora, G2, RB2B, UserGems, product analytics | Silos; nadie mide *time-to-touch* |
| Orquestación | Clay, n8n, Zapier, scripts | Sin versionado, sin tests, sin rollback, sin dueño |
| Ejecución | Smartlead, Instantly, HeyReach, Outreach, ads | Reputación de dominio gestionada a ojo |
| Medición | Dashboards de CRM, HockeyStack/Dreamdata | Atribución correlacional, indefendible ante finanzas |

**Coste oculto:** el 60-70% del tiempo del GTM Engineer se va en mantenimiento de fontanería, no en diseñar jugadas. Es exactamente el patrón que DevOps resolvió en infraestructura con IaC + CI/CD.

## Mapa competitivo y por qué existe hueco

| Jugador | Fortaleza | Ángulo muerto que Zolts explota |
|---|---|---|
| Clay | Orquestación de enriquecimiento, comunidad | Sin versionado/tests/gobierno; se rompe a escala; sin medición de incrementalidad |
| HubSpot / Salesforce (+IA nativa) | Distribución, sistema de registro | Lentos en ejecución multicanal y en política por jurisdicción; lock-in genera resistencia |
| AI SDR (11x, Artisan, Regie, AiSDR) | Demo espectacular | Caja negra, sin control de marca, comoditizado, castigado por deliverability |
| 6sense / Demandbase | Intent enterprise | Precio, opacidad del modelo, poca ejecución fina |
| Unify / Cargo / Default / Octave | Cercanos a la tesis | Aún no resuelven compliance por jurisdicción ni incrementalidad como primitiva |
| n8n / Workato / Tray | Flexibilidad | Genéricos: sin ontología GTM, sin señales, sin deliverability |

**Hueco defendible:** nadie ofrece a la vez (a) config-as-code con tests, (b) motor de políticas por jurisdicción, (c) holdouts e incrementalidad nativos, (d) routing de proveedores con optimizador de coste.

## Posicionamiento

> **Zolts es el runtime de GTM.** Tu CRM guarda lo que pasó. Zolts decide lo que va a pasar, lo ejecuta con gobierno y te demuestra cuánto de tu pipeline es incremental.

Anti-posicionamiento explícito (lo que NO somos): no somos un CRM, no somos una base de datos de contactos, no somos un AI SDR autónomo, no somos una herramienta de automatización genérica.

## Build / Buy / Partner

| Componente | Decisión | Justificación estructural |
|---|---|---|
| Runtime de ejecución durable | **Build** (sobre Temporal) | Es el núcleo del moat; nadie lo puede replicar sin reescribir su producto |
| DSL de programas + versionado | **Build** | Define la ontología; crea coste de cambio legítimo (no lock-in de datos) |
| Identity graph / resolución | **Build** | Calidad aquí determina todo lo demás; imposible externalizar |
| Policy engine jurisdiccional | **Build** | Moat regulatorio, ventaja europea, no lo tiene nadie |
| Medición / incrementalidad | **Build** | Cambia el comprador de Marketing a CFO → poder de precio |
| Datos de contacto/firmográficos | **Buy (multi-proveedor)** | Commodity; poseerlo es CAPEX + pasivo GDPR |
| Señales de terceros (intent, tech, hiring) | **Buy/Partner** | Coste marginal bajo, sin ventaja en originarlas |
| Infra de email (fase 1) | **Partner** (Smartlead/Instantly API) | Time-to-market; migrar a infra propia en fase 3 por margen |
| Infra de email (fase 3+) | **Build** | La capacidad de envío reputada se convierte en el recurso escaso |
| Dialer / voz | **Partner** | Mercado maduro, margen bajo |
| Ads (Meta/Google/LinkedIn) | **Partner (API)** | APIs estables, sin ventaja en reconstruirlas |
| CRM | **Nunca** | Guerra perdida contra distribución instalada |

## Efectos de segundo orden

1. **Entropía de canal:** los agentes IA multiplican el volumen de outbound → cae la tasa de respuesta del mercado → la reputación de envío y la relevancia por señal se vuelven el recurso escaso. Quien administre capacidad de envío como recurso gestionado (no como caja de dominios) captura el margen.
2. **Desplazamiento del comprador:** medir incrementalidad mueve la decisión de compra de Demand Gen a CFO/RevOps → ciclo más largo, ACV más alto, churn más bajo, poder de precio superior.
3. **Inversión de la cadena de valor de datos:** si Zolts enruta el gasto de enriquecimiento de N clientes, los proveedores pasan a ser proveedores commodity → arbitraje de márgenes y capacidad de negociar tarifas mayoristas.
4. **Regulación como foso:** el endurecimiento GDPR/AI Act expulsa a los competidores de "spray and pray" del mercado enterprise europeo; el policy engine se revaloriza con cada sanción publicada.
