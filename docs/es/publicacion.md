# Publicación de una versión (para mantenedores)

Cómo se publica una versión del harness, qué hace cada flujo de GitHub Actions y qué debe configurar el mantenedor a mano para publicar también en PyPI. La versión en inglés de los pasos de cuenta está en [Maintainer setup](../maintainer-setup.md).

## Canales de distribución

GitHub Packages no tiene un registro para paquetes de Python, así que el harness se distribuye por tres canales:

| Canal | Qué publica | Flujo | Estado |
|---|---|---|---|
| GitHub Releases | wheel, sdist, `SHA256SUMS`, SBOM SPDX, atestación de procedencia | `release.yml` | Siempre, con cada etiqueta `v*` |
| GHCR | Imagen `ghcr.io/sebasbarrera/harness-engineering` multi-arquitectura, con SBOM y atestación | `docker.yml` | Siempre, con cada etiqueta `v*` |
| PyPI | El mismo wheel y sdist, con un Trusted Publisher (sin tokens) | `release.yml`, trabajo `publish-pypi` | Solo si el mantenedor lo habilita (abajo) |

## El flujo de una versión

El flujo de ramas está en `CONTRIBUTING.md` y `CLAUDE.md`. Para una versión:

1. Desde `develop` crea `release/x.y.z`. Sube la versión en `pyproject.toml` (`version`) y en `src/governed_harness/__init__.py` (`__version__`): el flujo compara la etiqueta con `pyproject.toml`, y `harness --version` imprime `__version__`. Pasa el `CHANGELOG`: lo que estaba en `## Unreleased` pasa a una sección `## x.y.z`.
2. Espera a que todos los trabajos bloqueantes de todos los flujos estén en verde en la rama.
3. Mezcla `release/x.y.z` en `main` con un commit de merge (`git merge --no-ff`).
4. Crea la etiqueta anotada y sin firma `vx.y.z` sobre ese commit y súbela. Es lo que dispara los flujos de publicación.

   ```bash
   git tag -a vx.y.z -m "vx.y.z"
   git push origin vx.y.z
   ```

5. Mezcla `main` de vuelta en `develop`.

Nunca muevas ni reescribas la etiqueta `v0.8.0` (el corte evaluado en la tesis) ni la historia publicada.

## Qué hace `release.yml`

Se ejecuta al subir una etiqueta `v*` (salvo `v0.8.0`). El trabajo `release` (nombre «build, attest and publish»):

1. Comprueba que la etiqueta coincide con la versión de `pyproject.toml`; si no, falla.
2. Construye el sdist y el wheel con `python -m build` y los revisa con `twine check --strict`.
3. Genera el SBOM SPDX con `anchore/sbom-action`.
4. Atestigua la procedencia de los distribuibles con `actions/attest-build-provenance`.
5. Guarda el wheel y el sdist como artefacto del flujo (`python-distributions`, 7 días), para el trabajo de PyPI.
6. Copia el SBOM a `dist/`, escribe `SHA256SUMS`, genera las notas desde los Conventional Commits (`scripts/release_notes.py`) y agrega a las notas la sección de verificación.
7. Crea el release de GitHub con todo lo de `dist/` (`gh release create --verify-tag`).

`docker.yml` hace en paralelo su trabajo: construye y escanea la imagen y, con una etiqueta `v*`, la publica en GHCR para `linux/amd64` y `linux/arm64` con SBOM y atestación.

### El trabajo `publish-pypi`

Va después de `release`, solo sobre etiquetas y solo si la variable del repositorio `PYPI_PUBLISH` vale `true`:

- `needs: release`: no se ejecuta si la construcción falló.
- `if: vars.PYPI_PUBLISH == 'true' && startsWith(github.ref, 'refs/tags/v')`: con la variable ausente o distinta de `true`, el trabajo se omite y el release se crea igual. Así el flujo no se rompe mientras el Trusted Publisher no está configurado.
- `environment: pypi`: el entorno de GitHub que vincula el publicador de confianza y permite exigir aprobación manual.
- `permissions: id-token: write` y nada más: es el permiso que PyPI usa para el intercambio OIDC. Este trabajo no recibe `contents` ni `attestations`, y el nivel superior del flujo sigue sin permisos (el trabajo `release` tiene los suyos para el release y la atestación).
- Descarga el artefacto `python-distributions` que construyó y atestó el trabajo `release` y lo sube con `pypa/gh-action-pypi-publish`, fijada por el SHA completo del commit de `v1.14.2` (`dc37677b2e1c63e2034f94d8a5b11f265b73ba33`). No reconstruye nada: lo que llega a PyPI es el mismo archivo del release.

El SHA se obtuvo con `gh api repos/pypa/gh-action-pypi-publish/git/ref/tags/v1.14.2` (una etiqueta anotada, cuyo objeto es `a892a5a…`) y de ahí `git/tags/<sha>` para llegar al commit; la última versión estable al hacerlo era `v1.14.2`.

## Configuración única en PyPI (a mano)

Esto no se puede automatizar: requiere tu cuenta de PyPI y de GitHub.

1. Crea una cuenta en [pypi.org](https://pypi.org) y activa la verificación en dos pasos.
2. Comprueba que el nombre `governed-agent-harness` esté libre en PyPI (no pude consultarlo sin red). Si lo ocupa otro proyecto, hay que cambiar `name` en `pyproject.toml`, la URL del entorno en `release.yml` y estas guías.
3. En PyPI ve a **Your account → Publishing** y agrega un publicador pendiente (*pending publisher*), porque el proyecto aún no existe:
   - Project name: `governed-agent-harness`
   - Owner: `SebasBarrera`
   - Repository name: `harness-engineering`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
4. En GitHub, en **Settings → Environments**, crea el entorno `pypi` (si no existe, GitHub lo crea la primera vez que se usa). Recomendado: agrégate como revisor obligatorio, para aprobar a mano cada publicación en PyPI, porque una versión publicada no se puede volver a subir.
5. Activa la publicación con la variable del repositorio:

   ```bash
   gh variable set PYPI_PUBLISH --body true --repo SebasBarrera/harness-engineering
   ```

6. Sube la siguiente etiqueta `v*` (el flujo del apartado anterior). El trabajo `publish-pypi` aparece en la ejecución de `release.yml`; la primera vez crea el proyecto en PyPI a partir del publicador pendiente.
7. Comprueba en un entorno limpio:

   ```bash
   python3.12 -m venv /tmp/pypi-check && /tmp/pypi-check/bin/pip install governed-agent-harness==x.y.z
   /tmp/pypi-check/bin/harness --version
   ```

Para desactivar la publicación, borra la variable (`gh variable delete PYPI_PUBLISH`) o ponla en otro valor. Las versiones que ya estén en PyPI no se retiran; se pueden marcar como *yanked* desde la página del proyecto.

La publicación solo ocurre para etiquetas posteriores a activar la variable: la etiqueta `v1.0.0` ya tiene su release y su flujo no contiene este trabajo, así que esa versión no estará en PyPI salvo que se publique aparte.

## Qué se comprobó

- `twine check --strict` pasó con el wheel y el sdist construidos de este repositorio (versión 1.0.0). El mismo comando está en `release.yml`.
- `actionlint` y `zizmor --offline` (la versión fijada en `lint.yml`, 1.30.1) no reportaron hallazgos sobre `release.yml` con el trabajo nuevo.
- No se pudo ejecutar el flujo completo: requiere una etiqueta, GitHub Actions y la configuración de PyPI del mantenedor. El camino sin la variable (el trabajo se omite) es el comportamiento de `if` de Actions con una variable ausente; no se observó en una ejecución real.
- Metadatos: el nombre `governed-agent-harness`, la descripción, el `README.md` como descripción larga, la licencia, las clasificaciones y las URL del proyecto están en `pyproject.toml`. `setuptools` reciente avisa de que `license = {text = ...}` está en desuso; es solo un aviso de construcción y se puede migrar a una expresión SPDX más adelante.
