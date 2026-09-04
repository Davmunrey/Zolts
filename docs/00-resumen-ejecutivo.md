# 00 — Resumen ejecutivo

## BLUF

1. El stack GTM está roto por **fragmentación**: 12-20 herramientas, lógica de negocio atrapada en tablas Clay sin versionado, créditos quemados sin P&L, atribución indefendible ante CFO.
2. Zolts no compite en datos (commodity, intensivo en capital, tóxico legalmente) ni en copy IA (comoditizado por LLMs). Compite en el **runtime de ejecución**: el grafo versionado que decide *a quién, cuándo, por qué canal y con qué mensaje*, y demuestra el incremento con holdouts.
3. La adaptabilidad "a cualquier empresa" se resuelve con **arquitectura de overlays** (Blueprint → Pack sectorial → Tenant → Programa), no con servicios a medida. Los servicios matan el múltiplo.
4. Trade-off central: **profundidad vertical vs. amplitud horizontal**. Recomendación: horizontal en primitivas, vertical en blueprints. Se vende vertical, se construye horizontal.
5. El activo escaso a 24 meses no es el dato ni el modelo: es la **capacidad de envío reputada** y el **dataset de outcomes**. Ambos se acumulan solo si se ejecuta desde el día 1.

## Trade-offs explícitos

| Decisión | Coste | Por qué se acepta |
|---|---|---|
| Warehouse-native (BYO data) | Ciclo de venta más largo, requiere madurez de datos del cliente | Elimina objeción de "secuestro de datos", habilita enterprise, reduce COGS de storage |
| No poseer datos B2B propios | Dependencia de proveedores | El routing multi-proveedor captura margen sin CAPEX ni riesgo GDPR de titularidad |
| Holdout obligatorio (5-10%) | Reduce volumen alcanzable, fricción comercial | Convierte al CFO en aliado; es la única defensa contra la guerra de atribución |
| Agentes con gating por evals | Menos "magia" de demo, más lento | Un solo email alucinado con un logo de cliente destruye la cuenta enterprise |
| Config-as-code sobre UI visual | Más ingeniería inicial | Sin versionado y tests no hay gobierno; sin gobierno no hay enterprise ni precio |

## Decisión recomendada

Construir el **runtime + policy engine + medición** (núcleo defendible). Comprar/partner en **datos, canales de envío y dialers** (commodity con proveedores maduros). Nunca construir: bases de datos de contactos propias, ni un CRM.

Wedge de entrada: **empresas B2B de 50-500 empleados con motion mixta (PLG + sales-led) en Europa**, donde el dolor de compliance y la fragmentación son máximos y los incumbentes US son más débiles.

## Lectura recomendada por rol

| Rol | Empezar por |
|---|---|
| CEO/Board | 00, 01, 12, 13 |
| CTO | 02, 03, 04, 07 |
| GTM Engineer / RevOps | 04, 05, 06, 09, 10 |
| Legal/DPO | 11 |
