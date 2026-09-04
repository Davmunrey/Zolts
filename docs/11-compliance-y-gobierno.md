# 11 — Compliance y gobierno

> **Nota:** este documento describe requisitos de producto e ingeniería. No sustituye asesoramiento legal. Cada policy pack debe ser validado por el asesor jurídico del cliente antes de activarse en producción.

## Tesis estratégica

La regulación no es un coste: es el foso. Cuanto más se endurece el entorno (GDPR, ePrivacy, AI Act, reglas de remitentes en volumen), más se expulsa del mercado enterprise europeo a las herramientas de "spray and pray". Zolts convierte el cumplimiento en **una función del producto que la competencia no puede añadir tarde**, porque exige que la política se evalúe *antes* de cada acción, en el núcleo del runtime.

## Policy engine

Toda acción externa atraviesa una evaluación bloqueante:

```
evaluate(subject, action, context) → { allow | deny | review, rule_key, rationale }
```

Entradas: jurisdicción efectiva del contacto (país de residencia, no del dominio), base legal declarada por canal, estado de consentimiento y su procedencia, listas de supresión y exclusión, límites de frecuencia, horas silenciosas locales, cuotas del tenant, presupuesto restante, y clasificación del contenido generado por IA.

Cada decisión se persiste en `policy_decision` (append-only, 24 meses). Un DPO puede responder "¿por qué esta persona recibió este mensaje?" con una consulta, no con una investigación.

## Matriz de jurisdicciones (extracto del policy pack v1)

| Jurisdicción | Email B2B | Llamada B2B | Notas de implementación |
|---|---|---|---|
| España | Interés legítimo con opt-out claro | Sujeta a lista Robinson | LSSI + LOPDGDD; verificación obligatoria contra lista de exclusión publicitaria |
| Alemania | Muy restrictivo en la práctica; consentimiento como criterio prudente | Restringida | El pack por defecto bloquea email frío B2B en DE salvo override documentado por el cliente |
| Francia | Interés legítimo B2B con opt-out y relación con la función profesional | Sujeta a Bloctel | El cargo debe ser relevante para la oferta: se valida en el segmento |
| Reino Unido | Suscriptores corporativos exentos de consentimiento (PECR); individuales y sociedades personalistas, no | Sujeta a TPS/CTPS | La forma jurídica de la cuenta cambia la regla: se modela como campo |
| Países Bajos / Nórdicos | Interés legítimo con opt-out | Variable | — |
| EE. UU. | CAN-SPAM: opt-out, dirección física, sin encabezados engañosos | TCPA para móviles y SMS | Riesgo alto en móvil: consentimiento previo obligatorio |
| Canadá | CASL: consentimiento (expreso o implícito con ventana) | — | Pack por defecto: bloqueo salvo consentimiento registrado |
| LATAM | Variable por país (LGPD en Brasil, LFPDPPP en México) | Variable | Packs por país, con opt-out por defecto |

El pack es **datos, no código**: se actualiza sin desplegar y se versiona. Un cambio regulatorio se propaga a todos los tenants con changelog y notificación al DPO.

## GDPR: implementación concreta

| Obligación | Implementación en producto |
|---|---|
| Base legal | Declarada por canal y por programa; sin base legal, el runtime no ejecuta |
| Interés legítimo (LIA) | Plantilla de evaluación guiada, almacenada y versionada por tenant y programa |
| Minimización | El enriquecimiento solo pide los campos que el programa declara necesitar |
| Exactitud | Cada valor conserva fuente, fecha y confianza; corrección propagada en cascada |
| Transparencia | Aviso de privacidad y origen del dato incluibles automáticamente en el primer contacto |
| Derechos (DSAR) | Búsqueda por identity graph → export/borrado en cascada + propagación a subencargados + certificado |
| Retención | TTL por clase de dato y jurisdicción, ejecutado por job auditado |
| Registro de actividades (ROPA) | Generado automáticamente desde la configuración real, no mantenido a mano |
| Subencargados | Registro con DPA, región y estado; alerta ante alta de proveedor nuevo |
| Transferencias internacionales | Residencia por región + control de qué proveedores pueden tratar datos de qué región |
| Seguridad | Cifrado en tránsito y reposo, PII en bóveda con claves por tenant, acceso con propósito registrado |

## EU AI Act: obligaciones aplicables

El uso GTM típico cae en la franja de **riesgo limitado** (obligaciones de transparencia), pero el diseño asume el escenario más exigente:

- **Divulgación de contenido generado por IA** configurable por jurisdicción y canal, con plantillas por defecto.
- **Trazabilidad del sistema**: modelo, versión de prompt, fuentes, evals y aprobador por cada pieza de contenido.
- **Supervisión humana significativa**: el gating por evals y los tiers con revisión humana son el mecanismo, no una casilla.
- **Documentación técnica del sistema de scoring**: explicabilidad por factor obligatoria (`explain: true` no es opcional en el motor).
- **Prohibiciones**: sin inferencia de categorías especiales de datos (salud, orientación, religión, afiliación sindical, ideología) ni scoring basado en ellas. Regla dura en el motor, no configuración.

## Seguridad y certificaciones (secuencia)

| Fase | Hito | Por qué en ese orden |
|---|---|---|
| Día 1 | Cifrado, RLS, MFA, registro de accesos, gestión de secretos | Coste marginal cero si se hace desde el inicio |
| Mes 3 | Pack GDPR completo (DPA, ROPA, subencargados, DSAR) | Desbloquea el mid-market europeo |
| Mes 6 | SOC 2 Type I + pentest externo | Precio de entrada a procurement |
| Mes 12 | SOC 2 Type II | Desbloquea enterprise |
| Mes 15+ | ISO 27001 (si el pipeline lo exige) | Solo bajo demanda real de pipeline; no antes |

## Gobierno interno del cliente

Roles (RBAC): `owner`, `gtm_engineer` (edita programas), `operator` (ejecuta y revisa), `analyst` (solo lectura), `dpo` (auditoría y veto sobre políticas, sin acceso a ejecución). El DPO puede bloquear un programa; nadie puede desbloquearlo sin su firma registrada. Aprobaciones obligatorias configurables por tier de compliance.
