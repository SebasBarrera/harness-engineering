# Proyecto nuevo (desde cero)

Esta guía parte de un directorio vacío y llega a un primer cambio aprobado. Los comandos se probaron con el harness 1.0.0 y el proveedor `simulated`. La guía en inglés [Greenfield](../guides/greenfield.md) muestra la misma historia con todas las salidas.

## 1. Crea el proyecto y su commit base

El ChangeSet de una ejecución se calcula contra el estado del repositorio al iniciarla. Parte de un árbol limpio y confirmado. El harness necesita un repositorio de Git con al menos un commit.

```bash
mkdir pricing-demo && cd pricing-demo
mkdir -p src/pricing tests
printf 'def apply_discount(subtotal: float, threshold: float, rate: float) -> float:\n    return subtotal\n' > src/pricing/__init__.py
printf 'from pricing import apply_discount\n\n\ndef test_below_threshold() -> None:\n    assert apply_discount(99, 100, 0.1) == 99\n' > tests/test_pricing.py
printf '[project]\nname = "pricing-demo"\nversion = "0.1.0"\n\n[tool.pytest.ini_options]\npythonpath = ["src"]\n' > pyproject.toml
printf '__pycache__/\n.pytest_cache/\n' > .gitignore
git init -b main
git add . && git commit -m "baseline"
```

Instala `pytest` en el entorno donde corre el harness: el perfil de Python ejecuta `python -m pytest` con el `python` que encuentre en el `PATH`. Para un proyecto Node.js crea un `package.json` con un script `test` (por ejemplo `"test": "node --test"`).

## 2. Inicializa e inspecciona

```bash
harness init
harness inspect
harness doctor --path .
harness config validate
harness project show
```

- `init` crea `.harness/project.yaml` y una tarea de ejemplo. Qué contiene cada archivo está en [Estructura generada](estructura-generada.md).
- `inspect` solo lee: muestra los perfiles detectados (en esta prueba, `python_default` con confianza 0,8 por `pyproject.toml`).
- `project show` dice si el harness trata el proyecto como nuevo o existente. En un directorio con solo `pyproject.toml` y un commit mostró `kind: new` con el motivo «no source file beyond scaffolding»; en el proyecto de arriba, con un archivo de código fuente, mostró `kind: existing`. Esa distinción decide si, con un agente real, el harness propone opciones de arquitectura (proyecto nuevo) o estudia la que ya existe (proyecto existente). Ver [Arquitecturas](arquitecturas.md).
- `config validate` imprime la configuración resuelta: perfil, flujo, validadores (aquí `python.pytest`, `python.ruff` y `python.mypy`) y capacidades.

## 3. Escribe la primera tarea

Una tarea dice qué cambiar y cómo se comprobará. Esta es la que usa el repositorio en su guía; guárdala como `task.yaml`:

```yaml
taskId: task_discount_rule
title: Apply a percentage discount above a threshold
intent: Apply a percentage discount only when the subtotal reaches the threshold.
requirements:
  - requirementId: req_discount
    text: A subtotal at or above the threshold is reduced by the rate; below it, it is unchanged.
acceptanceCriteria:
  - criterionId: ac_below
    text: A subtotal below the threshold is unchanged.
  - criterionId: ac_at_threshold
    text: A subtotal equal to the threshold is reduced by the rate.
constraints:
  - Keep the public signature of apply_discount.
implementation:
  mode: patch
  patches:
    - path: src/pricing/__init__.py
      operation: replace
      content: |
        def apply_discount(subtotal: float, threshold: float, rate: float) -> float:
            return subtotal * (1 - rate) if subtotal >= threshold else subtotal
    - path: tests/test_pricing.py
      operation: append
      content: |


        def test_req_discount_at_threshold() -> None:
            assert apply_discount(100, 100, 0.1) == 90
```

Observa dos cosas:

- Con `requirementTraceability: enforce` (lo que escribe `init`), cada requisito con identificador necesita una prueba cuyo nombre lo contenga, aquí `test_req_discount_at_threshold`.
- El bloque `implementation` con parches es solo para ejecuciones deterministas y demostraciones. Con un agente real lo omites: el agente escribe el código y el harness gobierna el resultado. Ver [Agentes externos](../guides/external-agents.md).

Registra la tarea:

```bash
harness task create --file task.yaml
```

## 4. Ejecuta, revisa y decide

```bash
harness run start --task task_discount_rule     # código 4: espera tu decisión
harness review --run latest
harness gate decide --run <run-id> --decision APPROVE \
  --change-set-digest sha256:<digest> --rationale "Criterios cubiertos por pruebas"
```

En la prueba, `run start` terminó con código 4, la compuerta quedó en `PASSED` y el `APPROVE` cerró la ejecución. El cierre creó la rama `harness/<run-id>` con un commit del cambio y dejó los archivos en el índice. Para integrar el cambio en `main`, haz el merge de esa rama o confirma tú los archivos del índice.

## 5. Y después

- Para cada cambio siguiente repite los pasos 3 y 4, o usa `harness do "descripción del cambio"`, que crea la tarea y la ejecuta en un solo comando. Necesita un agente real configurado: con el proveedor `simulated` la ejecución terminó en `FAILED` en `IMPLEMENTATION`, porque ese proveedor solo aplica los parches que declara una tarea.
- Para ver el avance, los bloqueos y las métricas: [Seguimiento](seguimiento.md).
- Cuando varias personas trabajen sobre el mismo repositorio: [Trabajo simultáneo](trabajo-simultaneo.md).
