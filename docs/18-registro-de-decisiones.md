# 18 — Registro de decisiones: preguntas y recomendaciones por defecto

## Cómo usar este documento

136 decisiones. **124 ya tienen recomendación por defecto: si no las contradices, se ejecutan así.** Las 12 marcadas 🔴 son bloqueantes y solo tú puedes responderlas — dependen de hechos (tu red, tu capital, tu apetito de riesgo) que ningún análisis sustituye.

Regla de uso: no respondas todo. Responde las 🔴, tacha los defaults con los que no estés de acuerdo, y el resto queda decidido. Una decisión no registrada se reabre cada seis semanas y cuesta más que una decisión mediocre registrada.

---

## A · Identidad y tesis

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| A1 🔴 | ¿Zolts es producto, o la plataforma interna de un servicio que vendes? | **Producto puro.** Servicios solo como descubrimiento, techo 20% del ingreso | Define el múltiplo: 3× ingresos (servicios) vs 10-15× (producto) |
| A2 | ¿Frontal contra Clay o capa por encima? | **Coexistir en fase 1** (importador de tablas Clay), sustituir en fase 3 | Atacar de frente sin producto maduro regala la comparación al líder |
| A3 | ¿Zolts decide o también ejecuta? | **Ejecuta.** | Decidir sin ejecutar = dashboard = precio de dashboard |
| A4 | ¿Marca única o suite de productos? | **Única.** Un nombre, una promesa | Suite prematura fragmenta el mensaje y el equipo |
| A5 🔴 | Objetivo a 3 años: ¿independiente o adquisición estratégica? | — | Independiente → invertir en autoservicio y margen. Adquisición → invertir en integraciones y datos que HubSpot/Salesforce no tengan |
| A6 🔴 | ¿Cuál es el "no" fundacional — qué no harás nunca aunque venda? | Propuesto: no vender datos, no scraping no soportado, no AI SDR de caja negra | Sin un "no" escrito, el primer cliente grande te redefine la compañía |
| A7 | ¿Abrir código de alguna parte? | **DSL + SDK de conectores en Apache 2.0; runtime cerrado** | Distribución y confianza sin regalar el moat |

## B · ICP y mercado

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| B1 🔴 | Geografía inicial | Depende de dónde esté tu red. Propuesto: **Iberia + LATAM** para velocidad de diseño, **UK/NL/DACH** para ACV | Vender fuera de tu red en fase 0 duplica el CAC y el ciclo |
| B2 | Idioma del producto | **UI en inglés por defecto, español disponible.** Docs en inglés | Producto solo en español cierra el 80% del mercado y la ronda |
| B3 | Tamaño de empresa objetivo | **50-500 empleados** | <50 no tiene presupuesto ni dolor; >500 tiene procurement de 9 meses |
| B4 🔴 | Vertical de entrada | Propuesto: **SaaS B2B + agencias/servicios profesionales** — salvo que tengas ventaja injusta en otro sector | La ventaja injusta gana a la lógica de mercado siempre en fase 0 |
| B5 | ¿Greenfield o rip & replace? | **Al que ya tiene stack y lo sufre** | El presupuesto ya existe; educar a un greenfield cuesta 3× |
| B6 🔴 | ¿Tienes 3 empresas que firmen piloto de pago en 30 días? Nombres | — | Si la respuesta es no, la fase 1 empieza por conseguirlos, no por construir |
| B7 | ¿Piloto gratuito o de pago? | **Siempre de pago.** 5 k€ / 3 meses | El piloto gratis genera feedback cortés e inútil |
| B8 | ¿Quién define el éxito del piloto? | **Por escrito, firmado, con métrica y umbral, antes de empezar** | Sin criterio previo, todo piloto "va bien" y ninguno convierte |
| B9 | ¿Aceptas clientes fuera del ICP? | **Máximo 1, si paga y no exige roadmap** | Cada excepción cuesta ~6 semanas de ingeniería no reutilizable |
| B10 | ¿Canal de agencias desde el inicio? | **No vender por canal antes del mes 9**, pero reclutar 2 agencias como design partners | Las agencias son tu mejor fuente de plantillas y tu peor canal prematuro |
| B11 | ¿Nombras a Clay en el pitch? | **Sí, explícitamente** | El enemigo conocido acorta la venta; la categoría nueva la alarga |
| B12 | ¿SAM real, no TAM de slide? | **Definir las ~30-50k empresas europeas con equipo GTM real** | Un TAM inflado produce un plan de contratación inflado |

## C · Producto y alcance

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| C1 | ¿Studio visual en fase 1? | **No.** YAML + CLI + Studio de solo lectura. Editor bidireccional en fase 2 | El editor visual es el 40% del esfuerzo de front y no valida la tesis |
| C2 | ¿Multi-workspace por cliente? | **Sí en el esquema desde el día 1, no en UI hasta que se pida** | Retrofitear multi-workspace es una migración de datos brutal |
| C3 | ¿CRM ligero propio para quien no tiene? | **No.** Objeto "pipeline" mínimo, nunca un CRM | Es la trampa que ha matado a decenas de herramientas GTM |
| C4 | ¿Inbox unificado de respuestas? | **Sí, fase 2** | Sin inbox eres software de background: nadie te abre a diario, y lo que no se abre se cancela |
| C5 | ¿App móvil? | **No.** Notificaciones y aprobaciones a Slack | — |
| C6 | ¿Slack/Teams como superficie de aprobación? | **Sí, Slack primero** | Las aprobaciones por email mueren; el tier 1 se atasca |
| C7 | ¿Marketplace de plantillas público? | **Privado hasta 20 clientes** | Un marketplace vacío señala producto vacío |
| C8 | ¿SQL crudo sobre el warehouse del cliente? | **Sí, con sandbox y límites** | Es exactamente lo que compra el GTM Engineer; quitárselo lo insulta |
| C9 | ¿API pública desde cuándo? | **Fase 3, versionada** | Una API pública temprana congela decisiones que aún deben cambiar |
| C10 | ¿Servidor MCP? | **Sí, lectura + simulación en fase 2**; acciones con efecto externo requieren aprobación en UI | Diferencial real en 2026 y coste bajo |
| C11 | ¿Soporte a agencias que gestionan N clientes? | **Sí: jerarquía org > workspace desde el esquema** | Sin ella pierdes tu mejor canal de distribución futuro |
| C12 | ¿Onboarding asistido o autoservicio? | **Asistido hasta el cliente 10; autoservicio obligatorio a partir de ahí** | Si el cliente 15 sigue necesitando ayuda, no tienes producto |
| C13 | ¿Y si el cliente no tiene warehouse? | **Modo Postgres gestionado por Zolts como fallback** | Exigir warehouse elimina ~60% del mid-market europeo |
| C14 | ¿Importador de Clay/Apollo/HubSpot? | **Sí, día 1** | Es el mayor reductor de fricción de entrada que existe |

## D · Datos e integraciones

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| D1 | ¿Qué CRM el día 1? | **HubSpot primero**, Salesforce segundo, Attio tercero | HubSpot domina el mid-market europeo, tu ICP |
| D2 | ¿Zero-copy o copia? | **Híbrido:** copia mínima operativa + zero-copy analítico | Copia total = objeción de gobierno; zero-copy total = latencia inviable |
| D3 | ¿Cuántos proveedores de datos firmas primero? | **Tres: uno de cobertura barato, uno premium, uno de verificación** | Con menos de tres, el router no tiene nada que optimizar |
| D4 | ¿Reventa de créditos o BYO API key? | **Ambas.** BYO desbloquea a quien ya tiene contrato; reventa da margen | Solo reventa pierde a los sofisticados; solo BYO mata el margen |
| D5 | ¿Aceptas mínimos de compromiso con proveedores? | **No hasta tener volumen.** Pagar más por unidad a cambio de flexibilidad | Un mínimo firmado en el mes 2 es caja muerta en el mes 6 |
| D6 | ¿Deanonimización web propia o partner? | **Partner** | Campo minado legal en UE; no es tu batalla |
| D7 | ¿Intent de terceros (Bombora/G2) en fase 1? | **No.** Caro y de baja precisión en mid-market europeo | Gasto grande con lift no demostrable |
| D8 | ¿Guardas PII o solo referencias? | **Bóveda cifrada con claves por tenant; minimizar siempre** | — |
| D9 | ¿Enriquecer antes o después del scoring de fit? | **Después, sin excepción** | Es el mayor ahorro de todo el sistema (30-50%) |
| D10 | TTL de caché por campo | Email 180d · móvil 365d · firmográficos 90d · tecnografía 45d | TTL largo abarata pero degrada precisión; estos son el equilibrio |
| D11 | ¿Compartir estadística de acierto entre tenants? | **Sí, solo métricas agregadas, nunca valores. Declarado en el DPA** | Cruzar esa línea es riesgo existencial |
| D12 | ¿Y si un proveedor pide exclusividad? | **Rechazar** | La exclusividad destruye la tesis del router |

## E · IA y agentes

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| E1 | ¿Modelo propio o API? | **API.** Fine-tuning solo para clasificación barata | Entrenar modelo propio en 2026 es quemar capital sin ventaja |
| E2 | ¿Router multi-modelo? | **Sí, desde fase 2** | Coste y resiliencia ante caídas |
| E3 | ¿El cliente puede traer su propio modelo/API key? | **Sí en Enterprise** | Requisito recurrente de procurement en banca y salud |
| E4 | ¿Auto-envío desde cuándo? | **Nunca en tier 1.** Tier 2 desde fase 3 con eval ≥0,85 | — |
| E5 | ¿Quién responde si el agente escribe algo falso? | **Cliente: contenido y base legal. Zolts: el sistema y sus guardarraíles.** Cláusula explícita en ToS | Sin reparto escrito, el primer incidente es litigio |
| E6 | ¿Divulgación de contenido IA? | **Configurable, activada por defecto en UE** | — |
| E7 | ¿Golden set global o por tenant? | **Global + 50 casos por tenant** | — |
| E8 🔴 | ¿Entrenas con datos de clientes? | Propuesto: **no por defecto; opt-in con contrapartida económica** | "No entrenamos con tus datos" es un argumento de venta enorme en 2026 — o un activo que regalas |
| E9 | ¿Voz con IA? | **No antes del mes 12** | Riesgo regulatorio y de marca desproporcionado al retorno |
| E10 | ¿Coste de IA repercutido o incluido? | **Repercutido y visible como créditos** | La transparencia de coste es diferencial frente a las cajas negras |

## F · Ejecución y canales

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| F1 | ¿Envías tú o el cliente con sus buzones? | **Fase 1: buzones del cliente vía partner. Fase 3: infra propia opcional** | Asumir la reputación ajena antes de tiempo te quema a ti |
| F2 | ¿Vendes dominios y buzones gestionados? | **Sí desde fase 3** | Margen alto sobre el recurso que será escaso |
| F3 | LinkedIn: ¿API, partner o nada? | **Partner con cuentas del cliente. Nunca automatización de navegador propia** | El bloqueo de cuentas de clientes es daño reputacional irreparable |
| F4 | ¿WhatsApp? | **Sí, vía BSP oficial**, para LATAM y servicios locales | Canal dominante en tus mercados de diseño |
| F5 | ¿Dialer propio? | **No. Partner, fase 3** | — |
| F6 | ¿Ads (audiencias sincronizadas)? | **Sí, fase 2** | Barato de construir, alto valor percibido, cubre el tier 3 |
| F7 | ¿Frequency cap global cross-programa? | **Obligatorio, no configurable a cero** | Sin él, tres programas simultáneos queman la cuenta |
| F8 | ¿Y si el cliente quiere enviar 10× lo recomendado? | **Límite duro con override firmado y registrado** | No seas cómplice silencioso: es tu reputación de plataforma |
| F9 | ¿Bloqueas dominios de clientes actuales y competidores? | **Sí, por defecto** | El email a un cliente existente es el incidente clásico |
| F10 | ¿Gestión de respuestas dentro del producto? | **Sí, fase 2** (ver C4) | — |

## G · Medición

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| G1 | ¿Holdout obligatorio sin excepción? | **Obligatorio con waiver escrito y registrado** | La excepción registrada disciplina más que la prohibición absoluta |
| G2 | ¿% de holdout por defecto? | **10%**, mínimo 5% | — |
| G3 | Unidad de aleatorización | **Cuenta** en B2B; persona en B2C; geo cuando hay contaminación | Aleatorizar mal invalida todo el experimento |
| G4 | ¿Clientes sin volumen para significancia? | **Experimento sombrilla multi-programa + decirlo explícitamente** | Fingir significancia destruye la credibilidad de toda la tesis |
| G5 | ¿Reportas resultados negativos? | **Sí** | Es tu mayor generador de confianza y tu mayor diferencial cultural |
| G6 | Métrica primaria por defecto | **Oportunidades creadas a 90 días**, no reuniones | Las reuniones se inflan; las oportunidades no |
| G7 | ¿Mantienes atribución multi-touch? | **Sí, etiquetada como diagnóstico correlacional** | Quitarla del todo genera resistencia del equipo de marketing |
| G8 | ¿Benchmark anonimizado entre clientes? | **Sí a partir de 20 clientes** | Activo de marketing y de producto de primer orden |

## H · Compliance y legal

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| H1 🔴 | ¿Zolts es encargado o corresponsable del tratamiento? | Propuesto: **encargado**. Requiere validación de abogado antes del primer contrato | Definirlo mal es riesgo existencial, no una cláusula más |
| H2 | ¿Residencia UE desde cuándo? | **En la arquitectura desde fase 1, activada en fase 3** | Retrofitear residencia es rehacer la infraestructura |
| H3 | ¿Bloqueas Alemania por defecto? | **Sí, con override documentado por el cliente** | La postura conservadora vende en enterprise; la permisiva te demanda |
| H4 | ¿SOC 2 cuándo? | **Type I mes 6, Type II mes 12** | Antes es prematuro; después bloquea pipeline |
| H5 | ¿DPO propio o fraccional? | **Fraccional** | — |
| H6 | ¿Seguro de RC profesional y cyber? | **Sí, antes del primer contrato enterprise** | — |
| H7 | ¿Quién responde por el uso que hace el cliente? | **El cliente del contenido y la base legal; Zolts del sistema.** Política de uso aceptable con derecho de corte | — |
| H8 | ¿Cortas a un cliente que spamea? | **Sí, escrito en el contrato** | Un cliente tóxico contamina la reputación de toda la plataforma |
| H9 | ¿Auditoría externa del policy engine? | **Sí, mes 12** | Convierte el compliance en material de marketing |
| H10 | Retención por defecto | **12 meses de PII no convertida** | — |

## I · Pricing y modelo de negocio

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| I1 | ¿Freemium, trial o solo pago? | **Trial de 14 días con créditos limitados. Nunca freemium** | El freemium con COGS variable es una sangría |
| I2 | ¿Precio público? | **Sí en Starter/Growth; contacto en Scale+** | Ocultar el precio del plan de entrada dispara el CAC |
| I3 | ¿Anual o mensual? | **Anual con 2 meses de descuento; mensual con +20%** | Caja y churn |
| I4 | ¿Crédito único o monedas separadas? | **Único** | La simplicidad de la unidad vende; la complejidad genera disputas |
| I5 | ¿Rollover de créditos? | **1 mes, máximo 50%** | Sin rollover genera resentimiento; ilimitado destruye el forecast |
| I6 | ¿Overage automático o corte? | **Overage con alerta al 80% y techo duro configurable** | Una factura sorpresa cuesta el cliente entero |
| I7 | ¿Asientos o ilimitado? | **Asientos de operador; visores gratis** | Cobrar por ver reduce la difusión interna, que es tu mejor vendedor |
| I8 | ¿Descuento a design partners? | **50% de por vida a cambio de referencia pública y caso de estudio, contractual** | El descuento sin contrapartida escrita no se cobra nunca |
| I9 | ¿€ o $? | **Ambos, precio local, no conversión** | — |
| I10 🔴 | Acto 2: ¿anclado a gasto gobernado o a pipeline incremental? | Propuesto: **híbrido — suelo sobre gasto gobernado + bonus por lift verificado** | Define si eres infraestructura o socio de riesgo: son dos compañías |
| I11 | ¿Cobras la implantación? | **Siempre** | Lo gratis no se implanta ni se usa |
| I12 | ¿Cuándo subes precios? | **Cliente 15: +30%, con grandfathering de 12 meses** | Subir tarde ancla toda la cohorte futura a un precio bajo |

## J · GTM propio de Zolts

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| J1 | ¿Founder-led sales hasta cuándo? | **Mes 7 o 30 clientes, lo que llegue después** | Contratar AE sin playbook validado quema 6 meses y 60 k€ |
| J2 | ¿Outbound, inbound o comunidad? | **Outbound propio (dogfooding) + contenido técnico de GTM engineering** | La comunidad es el canal natural de este ICP |
| J3 | ¿Quién escribe el contenido? | **El fundador y el forward-deployed engineer** | Contenido de operador, no de marketing: este ICP detecta lo segundo al instante |
| J4 | ¿Construir en público? | **Sí** | Canal más barato para este ICP y motor de reclutamiento |
| J5 | ¿Comunidades GTM/RevOps? | **Presencia desde el mes 1** | — |
| J6 | ¿Herramienta gratuita como imán? | **Sí: auditoría de gasto GTM o validador de deliverability** | Doble uso: capta leads **y** captura el baseline que necesita el Acto 2 |
| J7 | ¿Qué categoría empujas? | **"GTM Operating System".** Elegir una y repetirla mil veces | Cambiar de categoría cada trimestre reinicia el reconocimiento a cero |
| J8 | ¿Web de producto o manifiesto? | **Manifiesto + demo interactiva** | Este ICP compra tesis antes que features |
| J9 | ¿Demo pública sin registro? | **Sí, sandbox con datos ficticios** | — |
| J10 | ¿Métrica héroe en la web? | **Números medidos** (−X% coste por contacto, lift verificado), nunca adjetivos | — |
| J11 | ¿Programa de partners? | **Mes 9** | — |
| J12 | ¿Cuándo contratas marketing? | **Después del AE, mes 10** | — |

## K · Equipo y organización

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| K1 🔴 | ¿Tienes cofundador técnico? | — | Sin él, la fase 1 se convierte en contratar, no en construir; cambia todo el plan |
| K2 | ¿Quién es el dueño del producto? | **El fundador hasta el mes 12** | Delegar producto antes de encontrar el fit diluye la tesis |
| K3 | ¿Remoto, híbrido o presencial? | **Híbrido con núcleo en una ciudad** | La fase 0 necesita ancho de banda humano, no calendarios |
| K4 | ¿Pool de equity? | **12-15% para los cinco primeros** | — |
| K5 | ¿Seniors o juniors? | **Tres seniors, cero juniors, hasta el mes 12** | Un junior en fase 0 consume más tiempo del que aporta |
| K6 | ¿Cuándo el forward-deployed engineer? | **Primera contratación no fundadora** | Es el rol que impide la trampa de servicios |
| K7 | ¿Qué externalizas? | **Diseño y legal fraccional. Nunca el núcleo** | — |
| K8 | Idioma interno | **Inglés en código y documentación** | Contratar fuera del país sin reescribir nada |
| K9 | ¿Quién responde a las 3 a.m.? | **On-call formal desde el cliente 5** | Un fallo de envío nocturno sin dueño cuesta un logo |

## L · Financiación

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| L1 🔴 | ¿Bootstrapped, pre-seed o seed? | Propuesto: **pre-seed 700 k€-1,2 M€** | El COGS variable asfixia el bootstrapping en este modelo |
| L2 | ¿Cuándo levantas? | **Con 10 clientes y lift demostrado (mes 5-7)** | Antes vendes slides y te valoran como slides |
| L3 | ¿Qué inversores? | **Angels que sean CRO/RevOps de empresas ICP** | Son simultáneamente capital, pipeline y validación |
| L4 | Runway mínimo aceptable | **18 meses post-ronda** | Menos convierte cada decisión en una decisión de supervivencia |
| L5 | ¿Venture debt? | **No en pre-seed** | — |
| L6 | ¿Subvenciones (ENISA, CDTI, EIC)? | **Sí, con gestión externalizada** | Dinero no dilutivo, pero la gestión interna devora al equipo |
| L7 | Dilución máxima en pre-seed | **15-20%** | — |
| L8 | ¿Qué hito compra la ronda siguiente? | **1 M€ ARR + NRR >110%** | Sin hito definido, la ronda se levanta por pánico de caja |

## M · Marca, dominio y propiedad intelectual

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| M1 🔴 | ¿`zolts.com` está disponible? Alternativas: `zolts.ai`, `getzolts.com` | **Verificar y comprar hoy** | Acción de coste bajo con ventana que se cierra sola |
| M2 | ¿Marca registrada? | **EUIPO clases 9 y 42 antes del lanzamiento público** (~850 €) | Rebrandear con 30 clientes cuesta 50× más |
| M3 | ¿Búsqueda de anterioridades? | **Antes de invertir un euro en marca** | — |
| M4 | ¿Handles sociales? | **Reservar hoy** | — |
| M5 | ¿IP del DSL abierta o cerrada? | Ver A7: **DSL abierto, runtime cerrado** | — |
| M6 | ¿Quién posee el código? | **La sociedad, con cesión de IP firmada por todos, freelances incluidos** | Sin esto, la due diligence de la ronda se cae |

## N · Riesgo y horizonte

| # | Pregunta | Recomendación por defecto | Consecuencia |
|---|---|---|---|
| N1 | ¿Qué te mata en 12 meses? | **Escribir las tres hipótesis mortales y testarlas primero, antes que las cómodas** | El orden de los tests determina si descubres el fallo con dinero o sin él |
| N2 | ¿Qué evidencia te haría pivotar? | **Definir el umbral hoy, en frío** | Definirlo cuando duele es racionalizar, no decidir |
| N3 | ¿Y si Clay lanza tu producto en 6 meses? | **Tener la respuesta escrita:** verticalización, compliance jurisdiccional, incrementalidad | — |
| N4 | ¿Vendes si te ofrecen 20 M€ en el mes 18? | **Definir el número hoy** | Negociar el precio en caliente destruye valor y relaciones |
| N5 | Concentración máxima por cliente | **25% del ARR a partir del mes 9** | — |
| N6 | ¿Y si el forward-deployed engineer se va? | **Su conocimiento vive en blueprints, no en su cabeza** — requisito de producto, no de RRHH | — |

---

## Las 12 bloqueantes, en orden de urgencia

| Orden | Decisión | Por qué bloquea |
|---|---|---|
| 1 | **M1** dominio y marca | Ventana que se cierra sola; coste trivial hoy |
| 2 | **K1** cofundador técnico | Determina si la fase 1 es construir o reclutar |
| 3 | **L1** capital y runway | Fija el tamaño del equipo y la agresividad del plan |
| 4 | **B6** tres design partners con nombre | Sin ellos la fase 1 no arranca; con ellos se autofinancia |
| 5 | **B4/B1** vertical y geografía de entrada | Tu ventaja injusta gana a cualquier análisis de mercado |
| 6 | **A1** producto vs. servicio | Define el múltiplo y la estructura de coste |
| 7 | **A5** independiente vs. adquisición | Redirige dónde se invierte ingeniería |
| 8 | **A6** el "no" fundacional | Sin él, el primer cliente grande te redefine |
| 9 | **H1** encargado vs. corresponsable | Riesgo existencial; requiere abogado antes del primer contrato |
| 10 | **E8** entrenar o no con datos del cliente | Argumento de venta o activo regalado; no ambos |
| 11 | **I10** ancla del Acto 2 | Define si eres infraestructura o socio de riesgo |
| 12 | **B7/B8** piloto de pago con criterio firmado | Determina si el feedback de fase 1 es real o cortés |

## Las 5 acciones de esta semana, independientes de todo lo anterior

1. Comprar dominio y reservar handles. Coste: <100 €.
2. Búsqueda de anterioridades de marca en EUIPO. Coste: 0.
3. Escribir la lista de 30 empresas objetivo con nombre y contacto. Coste: 4 horas.
4. Conversación con abogado sobre H1 (encargado vs. corresponsable). Coste: ~300 €.
5. Escribir en una página el "no" fundacional (A6) y las tres hipótesis mortales (N1). Coste: 2 horas.

Ninguna depende de tener producto, equipo ni capital. Todas se degradan con el tiempo.
