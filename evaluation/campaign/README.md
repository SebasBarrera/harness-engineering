# Campaña de evaluación de la 2.0.0

La evaluación de la tesis reporta solo la versión 2.0.0 (campaña en curso). Los resultados de versiones anteriores están en el historial de git y en el tag `v0.8.0`, y no forman parte de este análisis.

Scripts, catálogo y plan de la campaña que evalúa la versión 2.0.0 del harness. Todo lo de esta
carpeta se versiona: la carpeta local `.local/` donde vivían los scripts de comparación se perdió
con el equipo anterior, y no debe volver a pasar.

| Archivo | Qué es |
|---|---|
| [`CATALOGO.md`](CATALOGO.md) | Inventario de las pruebas existentes y 55 pruebas nuevas propuestas (H01–H55), con hipótesis, método, métricas, criterio de éxito, costo y sesgos; plan y reglas de integridad. |
| `run_deterministic.py` | Corre todas las suites deterministas (sin modelo) sobre un wheel: crea los entornos, verifica el archivo de `itsdangerous` y registra el código de salida de cada suite en `campaign-summary.json`. Sale con 1 si alguna falló. |
| `../../.github/workflows/evaluation.yml` | El mismo script en Linux de GitHub Actions, con `bwrap` (sandbox real), lanzado a mano sobre la rama o etiqueta a evaluar (`workflow_dispatch`, «Use workflow from»): esa rama debe traer estos scripts. Los resultados quedan como artefacto para revisarlos y versionarlos. |

## Reglas de integridad

- Los criterios de éxito se declaran antes de correr (`PREREGISTRO.md`, por crear a partir del
  catálogo) y no se cambian después de ver resultados.
- **Toda corrida se reporta.** Ninguna prueba se descarta por dar un resultado desfavorable al
  harness; solo se excluyen corridas por causas técnicas declaradas (por ejemplo, el límite de uso
  de la cuenta), con su causa en el registro. Los costos del harness (tiempo, tokens, fricción) se
  presentan como el precio de la gobernanza, junto con lo que compran y la configuración que los
  mitiga.
- La persona simulada es una simulación declarada, no una persona.
- Ninguna cifra mezcla versiones, máquinas ni sistemas operativos: cada resultado lleva su
  `environment.json` (versión, SHA-256 del wheel, commit, plataforma).

## Entornos

| Entorno | Qué corre | Limitaciones declaradas |
|---|---|---|
| GitHub Actions, `ubuntu-24.04` | Suites deterministas (sin modelo), con el sandbox de escritura real (`bwrap`). | Runners compartidos: los tiempos absolutos son ruidosos; se comparan condiciones dentro de la misma corrida. |
| Windows 10 (equipo actual, sin virtualización) | Corridas con Claude real (piloto y bloques), con la sesión local del CLI. | No hay sandbox de escritura en Windows: las condiciones gobernadas corren con `runtime.agentSandbox: off`, declarado en cada registro como desviación de lo que escribe `harness init`. El código de evaluación con modelo es POSIX y necesita adaptarse. Máquina de 4 núcleos y 12 GB: los tiempos no son comparables con los de otro equipo o sistema operativo. |
| **macOS (pendiente: cuando el autor compre una Mac)** | **La campaña completa en un solo equipo**: suites deterministas y todos los bloques con modelo, con `sandbox-exec` y los lanzadores de `evaluation/launchers/` sin cambios. | — |

### Pendiente para la Mac

Cuando haya una Mac disponible:

1. Instalar Python 3.12, Git, Node.js y el CLI de Claude Code autenticado con la cuenta de
   evaluación (`agentlib.EXPECTED_ACCOUNT`).
2. Correr `run_deterministic.py` sobre el wheel `v2.0.0` publicado y comparar con los resultados
   de GitHub Actions (mismo código, otro sistema operativo).
3. Repetir el piloto y los bloques A, B, D y embebido con `evaluation/launchers/*.sh`, con el
   sandbox en `enforce` como lo escribe `harness init`, y reemplazar los resultados de Windows en
   la tesis por los de macOS, o presentarlos como réplica.
4. Implementar y correr en la Mac las pruebas nuevas del catálogo que dependen de `sandbox-exec`.

Se sigue en el issue #113.
