# Backlog priorizado

## Convenciones

- P0: bloquea el primer vertical slice.
- P1: requerido para el MVP evaluable.
- P2: posterior al MVP o spike acotado.

## ADR-001 - Aprobar límites del sistema

**ID:** ADR-001  
**Epic:** ADRs y fundamentos  
**Título:** Aprobar límites del sistema  
**Objetivo:** Convertir el límite harness/agente/modelo/tool/CI en una decisión aceptada.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** Ninguna  
**Prioridad:** P0  
**Esfuerzo:** S  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## ADR-002 - Aprobar arquitectura híbrida

**ID:** ADR-002  
**Epic:** ADRs y fundamentos  
**Título:** Aprobar arquitectura híbrida  
**Objetivo:** Fijar core, perfiles, extensiones, configuración y políticas bloqueadas.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-001  
**Prioridad:** P0  
**Esfuerzo:** S  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## ADR-003 - Aprobar fases normativas

**ID:** ADR-003  
**Epic:** ADRs y fundamentos  
**Título:** Aprobar fases normativas  
**Objetivo:** Fijar fases, estados, loops, invalidaciones y retrospectiva separada.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## ADR-004 - Aprobar confianza y capacidades

**ID:** ADR-004  
**Epic:** ADRs y fundamentos  
**Título:** Aprobar confianza y capacidades  
**Objetivo:** Definir default deny, scopes, expiración y acciones privilegiadas.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## ADR-005 - Aprobar modelo de extensiones

**ID:** ADR-005  
**Epic:** ADRs y fundamentos  
**Título:** Aprobar modelo de extensiones  
**Objetivo:** Definir detector, profile, plugin, package y contract test kit.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-002  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## DOM-001 - IDs, estados y taxonomía de errores

**ID:** DOM-001  
**Epic:** Modelo de dominio  
**Título:** IDs, estados y taxonomía de errores  
**Objetivo:** Implementar enumeraciones cerradas y tipos de identidad.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-003  
**Prioridad:** P0  
**Esfuerzo:** S  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## DOM-002 - Task y aceptación

**ID:** DOM-002  
**Epic:** Modelo de dominio  
**Título:** Task y aceptación  
**Objetivo:** Implementar Task, Requirement y AcceptanceCriterion versionados.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** DOM-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## DOM-003 - Run, PhaseExecution y ChangeSet

**ID:** DOM-003  
**Epic:** Modelo de dominio  
**Título:** Run, PhaseExecution y ChangeSet  
**Objetivo:** Implementar agregados mínimos y reglas de ciclo de vida.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** DOM-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## SCH-001 - Common y execution schemas

**ID:** SCH-001  
**Epic:** Schemas  
**Título:** Common y execution schemas  
**Objetivo:** Crear contratos JSON Schema para IDs, estados y ejecución.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** DOM-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## SCH-002 - Evidence, artifact y event schemas

**ID:** SCH-002  
**Epic:** Schemas  
**Título:** Evidence, artifact y event schemas  
**Objetivo:** Crear provenance, digests y hash-chain fields.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SCH-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## SCH-003 - Validation, finding y gate schemas

**ID:** SCH-003  
**Epic:** Schemas  
**Título:** Validation, finding y gate schemas  
**Objetivo:** Normalizar validadores y decisiones fail-closed.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SCH-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## SCH-004 - Decision, ChangeSet e invocation schemas

**ID:** SCH-004  
**Epic:** Schemas  
**Título:** Decision, ChangeSet e invocation schemas  
**Objetivo:** Completar contratos del vertical slice.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SCH-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## SM-001 - Transiciones normativas

**ID:** SM-001  
**Epic:** Máquina de estados  
**Título:** Transiciones normativas  
**Objetivo:** Implementar guardas y tabla explícita de transiciones.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-003, DOM-003  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## SM-002 - Invalidación y reanudación

**ID:** SM-002  
**Epic:** Máquina de estados  
**Título:** Invalidación y reanudación  
**Objetivo:** Invalidar evidencia/aprobación stale y reanudar idempotentemente.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SM-001, EVT-001  
**Prioridad:** P0  
**Esfuerzo:** L  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## EVT-001 - SQLite append-only event store

**ID:** EVT-001  
**Epic:** Event store  
**Título:** SQLite append-only event store  
**Objetivo:** Persistir eventos atómicos con secuencia y hash previo.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SCH-002  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## EVT-002 - Projector de estado y trace

**ID:** EVT-002  
**Epic:** Event store  
**Título:** Projector de estado y trace  
**Objetivo:** Reconstruir estado, timeline y causalidad desde eventos.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** EVT-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4/OE5

## ART-001 - Content-addressed local store

**ID:** ART-001  
**Epic:** Artifact store  
**Título:** Content-addressed local store  
**Objetivo:** Put/get/verify con SHA-256, media type, redacción y retención.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SCH-002  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## CLI-001 - Capa de aplicación compartida

**ID:** CLI-001  
**Epic:** CLI  
**Título:** Capa de aplicación compartida  
**Objetivo:** Crear use cases sin lógica de presentación.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** DOM-003, SM-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4

## CLI-002 - init, inspect y task create

**ID:** CLI-002  
**Epic:** CLI  
**Título:** init, inspect y task create  
**Objetivo:** Soportar modo humano y JSON no interactivo.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** CLI-001, PRO-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4

## CLI-003 - run, status, trace y gate decide

**ID:** CLI-003  
**Epic:** CLI  
**Título:** run, status, trace y gate decide  
**Objetivo:** Completar comandos del vertical slice y códigos de salida.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** CLI-001, EVT-002, GAT-002  
**Prioridad:** P0  
**Esfuerzo:** L  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4

## EXE-001 - Process runner seguro

**ID:** EXE-001  
**Epic:** Execution engine  
**Título:** Process runner seguro  
**Objetivo:** argv, shell false, cwd contenido, env allowlist, timeout y output bounds.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** CAP-001, ART-001  
**Prioridad:** P0  
**Esfuerzo:** L  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## EXE-002 - Cancelación y cleanup

**ID:** EXE-002  
**Epic:** Execution engine  
**Título:** Cancelación y cleanup  
**Objetivo:** Cancelar árbol de procesos y registrar cleanup incompleto.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** EXE-001  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4

## GIT-001 - Adapter Git y digest

**ID:** GIT-001  
**Epic:** Git y ChangeSet  
**Título:** Adapter Git y digest  
**Objetivo:** Snapshot base/head, ownership de paths y digest estable.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** EXE-001, ART-001  
**Prioridad:** P0  
**Esfuerzo:** L  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## CAP-001 - Capability grants

**ID:** CAP-001  
**Epic:** Capacidades y seguridad  
**Título:** Capability grants  
**Objetivo:** Autorizar actor, scope, duración, condiciones y revocación.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-004, DOM-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## CAP-002 - Path y symlink containment

**ID:** CAP-002  
**Epic:** Capacidades y seguridad  
**Título:** Path y symlink containment  
**Objetivo:** Bloquear escapes y escribir security fixtures.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** CAP-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## SEC-001 - Threat-model test pack

**ID:** SEC-001  
**Epic:** Capacidades y seguridad  
**Título:** Threat-model test pack  
**Objetivo:** Injection, secrets, stale approval, output bomb y malicious plugin.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** CAP-002, EXE-001  
**Prioridad:** P1  
**Esfuerzo:** L  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE5

## VAL-001 - Validator contract y normalización

**ID:** VAL-001  
**Epic:** Validadores  
**Título:** Validator contract y normalización  
**Objetivo:** detect/execute/parse/normalize y errores diferenciados.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SCH-003, EXE-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## VAL-002 - Validador Python

**ID:** VAL-002  
**Epic:** Validadores  
**Título:** Validador Python  
**Objetivo:** pytest y checks opcionales detrás del profile.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** VAL-001, PRO-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4/OE5

## VAL-003 - Validador Node.js

**ID:** VAL-003  
**Epic:** Validadores  
**Título:** Validador Node.js  
**Objetivo:** test/lint/typecheck detrás del profile.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** VAL-001, PRO-002  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4/OE5

## GAT-001 - Gate engine determinista

**ID:** GAT-001  
**Epic:** Gates  
**Título:** Gate engine determinista  
**Objetivo:** Consolidar resultados y políticas sin ejecutar tools.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SCH-003, VAL-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## GAT-002 - Decisión humana ligada al digest

**ID:** GAT-002  
**Epic:** Gates  
**Título:** Decisión humana ligada al digest  
**Objetivo:** Aprobar/rechazar/solicitar cambios y detectar staleness.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** GAT-001, GIT-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4/OE5

## AGT-001 - Contrato de provider

**ID:** AGT-001  
**Epic:** Agent providers  
**Título:** Contrato de provider  
**Objetivo:** start/resume/cancel/events/usage con schema y capability manifest.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** SCH-004, CAP-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## AGT-002 - Provider simulado

**ID:** AGT-002  
**Epic:** Agent providers  
**Título:** Provider simulado  
**Objetivo:** Patcher determinista para vertical slice y fault injection.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** AGT-001  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4/OE5

## AGT-003 - Primer provider real

**ID:** AGT-003  
**Epic:** Agent providers  
**Título:** Primer provider real  
**Objetivo:** Integración escogida después de aprobar retención y permisos.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** AGT-001, TEL-002  
**Prioridad:** P1  
**Esfuerzo:** L  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4/OE5

## PRO-001 - Detector y profile Python

**ID:** PRO-001  
**Epic:** Perfiles tecnológicos  
**Título:** Detector y profile Python  
**Objetivo:** Markers, commands, validators y safe defaults.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-005  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## PRO-002 - Detector y profile Node.js

**ID:** PRO-002  
**Epic:** Perfiles tecnológicos  
**Título:** Detector y profile Node.js  
**Objetivo:** Segundo stack sin cambios al core.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-005, VAL-003  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4/OE5

## MEM-001 - Context manifest determinista

**ID:** MEM-001  
**Epic:** Memoria  
**Título:** Context manifest determinista  
**Objetivo:** Seleccionar normativa, task y project records por relación, vigencia y presupuesto.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** DOM-002, ART-001  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## MEM-002 - Project memory records

**ID:** MEM-002  
**Epic:** Memoria  
**Título:** Project memory records  
**Objetivo:** Provenance, contradicción, promoción y olvido revisado.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** MEM-001  
**Prioridad:** P2  
**Esfuerzo:** L  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## TEL-001 - Projector de métricas operacionales

**ID:** TEL-001  
**Epic:** Telemetría  
**Título:** Projector de métricas operacionales  
**Objetivo:** Duraciones, intentos, ciclos, waits, gates y trace completeness.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** EVT-002  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE5

## TEL-002 - Uso de provider y costos

**ID:** TEL-002  
**Epic:** Telemetría  
**Título:** Uso de provider y costos  
**Objetivo:** Registrar solo datos observables; cost derived con pricing snapshot.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** AGT-001  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE5

## RET-001 - Reglas deterministas

**ID:** RET-001  
**Epic:** Retrospectiva  
**Título:** Reglas deterministas  
**Objetivo:** Observación a recomendación sin mutación automática.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** TEL-001, EVT-002  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE5

## WEB-001 - API/read-only status mínima

**ID:** WEB-001  
**Epic:** Web  
**Título:** API/read-only status mínima  
**Objetivo:** Vista complementaria después del vertical slice; no lógica nueva.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** CLI-001, EVT-002  
**Prioridad:** P2  
**Esfuerzo:** L  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4

## MCP-001 - Spike de contrato MCP

**ID:** MCP-001  
**Epic:** Agent providers  
**Título:** Spike de contrato MCP  
**Objetivo:** Validar schema, capabilities y audit; sin meter runtime en MVP.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** CAP-001, VAL-001  
**Prioridad:** P2  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3

## FIX-001 - Fixture Python greenfield

**ID:** FIX-001  
**Epic:** Fixtures  
**Título:** Fixture Python greenfield  
**Objetivo:** Repo reseteable con hidden tests y task pairs.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** PRO-001, VAL-002  
**Prioridad:** P0  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE5

## FIX-002 - Fixture Node brownfield

**ID:** FIX-002  
**Epic:** Fixtures  
**Título:** Fixture Node brownfield  
**Objetivo:** Baseline conocido, architecture rule y hidden regression.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** PRO-002, VAL-003  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE5

## EVA-001 - Protocolo greenfield

**ID:** EVA-001  
**Epic:** Evaluación  
**Título:** Protocolo greenfield  
**Objetivo:** Variables, ground truth, reset, instrumentos y analysis plan.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** FIX-001, TEL-001  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE5

## EVA-002 - Protocolo brownfield

**ID:** EVA-002  
**Epic:** Evaluación  
**Título:** Protocolo brownfield  
**Objetivo:** Control de deuda preexistente, location correctness y review effort.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** FIX-002, TEL-001  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE5

## DOC-001 - Guía de arquitectura y extensión

**ID:** DOC-001  
**Epic:** Documentación  
**Título:** Guía de arquitectura y extensión  
**Objetivo:** Mantener generated diagrams y contract examples.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** ADR-005, VAL-001  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE3/OE4

## DOC-002 - Guía de uso, seguridad y evaluación

**ID:** DOC-002  
**Epic:** Documentación  
**Título:** Guía de uso, seguridad y evaluación  
**Objetivo:** Onboarding reproducible y límites declarados.  
**Descripción:** Implementar el alcance indicado respetando contratos versionados, provenance, fail-closed y la regla de dependencias del core.  
**Criterios de aceptación:**

- Existe contrato o ADR versionado cuando aplica.
- El caso feliz y al menos un caso de error/bloqueo están automatizados.
- No se introduce lógica tecnológica en dominio u orquestación.
- Los outputs relevantes validan contra schema y contienen provenance.
**Dependencias:** CLI-003, SEC-001, EVA-001  
**Prioridad:** P1  
**Esfuerzo:** M  
**Riesgos:** acoplamiento accidental, semántica ambigua, fail-open o duplicación de autoridad.  
**Tests:** unitarios; contract tests; integración o seguridad según el epic.  
**Artefactos:** código, schemas, fixtures, eventos y documentación del issue.  
**Objetivo académico relacionado:** OE4/OE5

## Primer issue

ADR-001.

## Primeros cinco issues

ADR-001, ADR-002, ADR-003, ADR-004 y ADR-005.

## Primer milestone

ADRs aceptados, schemas comunes, state machine, event/artifact stores, CLI shell, provider simulado y una ejecución Python end-to-end con gate humano ligado al digest.

## No iniciar todavía

WEB-001, MCP-001, MEM-002 y cualquier marketplace, ejecución distribuida, GraphRAG, DAST genérico, multiusuario o auto-optimización.
