# Respaldo privado del trabajo de la tesis

Esta rama no se fusiona a `develop`. Solo deja escrito dónde quedó el material que no puede vivir en este repositorio público, para retomar el trabajo desde otro computador.

## Dónde está

- Repositorio: **`SebasBarrera/harness-engineering-respaldo`** (privado).
- URL: <https://github.com/SebasBarrera/harness-engineering-respaldo>
- Solo lo ve el dueño de la cuenta. Para verlo hay que iniciar sesión en GitHub con esa cuenta o usar `gh auth login` y luego:

```bash
gh repo clone SebasBarrera/harness-engineering-respaldo respaldo
```

## Qué contiene

| Ruta en el respaldo | Qué es | Origen |
|---|---|---|
| `PLAN-CONTINUACION.md` | Estado, resultados de la campaña, decisiones pendientes, qué sigue y el paso a paso para un computador nuevo. Empieza por aquí. | Redactado al cerrar el computador anterior |
| `README.md` | Índice del respaldo y cómo reconstruir las corridas. | — |
| `local/` | Copia de la carpeta `.local/` de este repositorio: bitácoras de agentes, borradores de tablas de resultados, presentación de la sustentación de avances y revisión del consejo. | `.local/` |
| `campana/out/` | Salidas de la campaña de evaluación 2.0.1 (registros, puntajes, resúmenes por bloque). | Directorio `EVAL_OUT` |
| `campana/runs/` | Corridas crudas completas, empaquetadas en `tar.gz` partido. | Directorio `EVAL_WORK/runs` |
| `campana/scripts/` | Scripts de lanzamiento de la campaña (tienen rutas del equipo anterior; hay que ajustarlas). | — |
| `claude/` | Memoria persistente y transcripciones de Claude Code. | — |
| `worktrees-sin-commit/` | Archivos que estaban sin confirmar en algún worktree. | — |
| Ramas `ramas/*` | Copia de todas las ramas locales, incluidas las que nunca se subieron aquí. | `git push respaldo 'refs/heads/*:refs/heads/ramas/*'` |

## Cómo retomar

1. Clonar este repositorio y el respaldo.
2. Seguir `PLAN-CONTINUACION.md`, sección «Paso a paso para un computador nuevo».
3. Copiar `respaldo/local/` a `harness-engineering/.local/` (la carpeta sigue fuera de Git; ver `.github/privacy-denylist.txt`).

El respaldo no contiene llaves, tokens ni contraseñas: se escaneó antes de subirlo. Las credenciales (sesión de Claude Code, `gh`, variables como `HARNESS_API_TOKEN`) se configuran de nuevo en el computador nuevo, nunca en un repositorio.
