# Instalación avanzada

Opciones para quien necesita más control: pipx, extras, una versión concreta, verificación de integridad, instalación sin red, actualización y desinstalación. Primero lee la [instalación básica](instalacion.md).

Qué se comprobó en esta guía: la instalación del wheel construido de este repositorio sin red, `harness --version`, `pip show`, la desinstalación y la verificación de sumas, todo con pip y un entorno virtual nuevo. Lo que depende de internet (PyPI, GitHub Releases, GHCR, `gh attestation`, pipx) no se pudo ejecutar en el entorno de prueba y se marca así. Los comandos de ese tipo son los estándares de cada herramienta.

## Instalación aislada con pipx

pipx instala el comando `harness` en su propio entorno, sin mezclarlo con las dependencias de tus proyectos:

```bash
pipx install governed-agent-harness
pipx install "governed-agent-harness[api]"
```

Desde el wheel de un release:

```bash
pipx install "https://github.com/SebasBarrera/harness-engineering/releases/download/v1.0.0/governed_agent_harness-1.0.0-py3-none-any.whl"
```

(No ejecutado: pipx no está instalado en el entorno de prueba.)

Una advertencia: los validadores del proyecto (`python -m pytest`, `ruff`, `mypy`) se ejecutan con el intérprete y las herramientas del `PATH` o del `.venv` del proyecto, no con las del entorno de pipx. Instala las herramientas de prueba en el proyecto, no en el entorno del harness.

## Extras

| Extra | Instala | Para qué |
|---|---|---|
| `api` | FastAPI y Uvicorn | El tablero web local (`harness api serve`) |
| `all` | Lo mismo que `api` hoy | Atajo |
| `dev` | pytest, cobertura, ruff, mypy, httpx, build y tipos | Desarrollar el harness |

```bash
pip install "governed-agent-harness[api]"
```

## Una versión concreta

Con PyPI:

```bash
pip install "governed-agent-harness==1.0.0"
pip index versions governed-agent-harness      # lista las versiones publicadas
```

Con el release de GitHub, cambia la versión en la URL del wheel (`v<versión>` en la carpeta y `<versión>` en el nombre del archivo):

```bash
pip install "governed-agent-harness @ https://github.com/SebasBarrera/harness-engineering/releases/download/v1.0.0/governed_agent_harness-1.0.0-py3-none-any.whl"
```

Con Docker, la etiqueta es la versión completa (`1.0.0`) o la mayor y la menor (`1.0`):

```bash
docker run --rm ghcr.io/sebasbarrera/harness-engineering:1.0.0 --version
```

Las etiquetas salen del flujo `docker.yml` (`type=semver,pattern={{version}}` y `{{major}}.{{minor}}`). La lista de versiones está en las [releases](https://github.com/SebasBarrera/harness-engineering/releases) y en el [CHANGELOG](https://github.com/SebasBarrera/harness-engineering/blob/develop/CHANGELOG.md).

## Verificar las sumas y la atestación

Cada release del repositorio incluye los distribuibles, el archivo `SHA256SUMS` y un SBOM en formato SPDX (`governed-agent-harness.spdx.json`). Además, el flujo `release.yml` atestigua la procedencia de construcción de los distribuibles (`actions/attest-build-provenance`), es decir, firma que salieron de ese flujo y de ese commit.

1. Descarga el wheel y `SHA256SUMS` en la misma carpeta (a mano desde la página del release, o con la CLI de GitHub).

   ```bash
   gh release download v1.0.0 --repo SebasBarrera/harness-engineering
   ```

2. Comprueba las sumas.

   ```bash
   sha256sum -c SHA256SUMS            # Linux
   shasum -a 256 -c SHA256SUMS        # macOS
   ```

   Si descargaste solo algunos archivos, la comprobación avisa de los que faltan; lo importante es que los que están digan `OK`. En la prueba, con los dos archivos construidos localmente, la salida fue `…-py3-none-any.whl: OK` y `….tar.gz: OK`.

3. Verifica la atestación con la CLI de GitHub:

   ```bash
   gh attestation verify governed_agent_harness-1.0.0-py3-none-any.whl --repo SebasBarrera/harness-engineering
   ```

   Es el mismo comando que `release.yml` agrega a las notas de cada release. No lo pude ejecutar sin red.

Si publicas en PyPI con el Trusted Publisher, la acción `pypa/gh-action-pypi-publish` genera además atestaciones de PyPI (PEP 740) por defecto cuando publica con OIDC, según su documentación; no lo he verificado porque la publicación aún no está activa. El wheel que sube a PyPI es el mismo archivo que construyó, atestó y adjuntó el trabajo `release`, así que las sumas coinciden.

## Instalar sin red (wheel local)

El wheel solo no basta: el harness necesita sus dependencias (`pydantic`, `typer`, `PyYAML`, `jsonschema`, `referencing`). Con una máquina conectada, descarga todo en una carpeta:

```bash
pip download governed-agent-harness==1.0.0 -d wheelhouse     # no ejecutado: requiere red
```

Copia `wheelhouse` a la máquina sin red e instala desde ahí:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install --no-index --find-links wheelhouse governed-agent-harness
```

Comprobado en la prueba: `pip install --no-index --find-links <carpeta con solo el wheel> governed-agent-harness` falla con `No matching distribution found for pydantic<3,>=2.8`, que confirma que las dependencias deben estar en la carpeta. En cambio, `pip install --no-index --no-deps <wheel>` instaló el paquete y `harness --version` imprimió `harness 1.0.0` cuando las dependencias ya estaban disponibles en el entorno.

## Actualizar

No existe un comando `harness self update`: la actualización es la de tu gestor de paquetes.

```bash
pip install --upgrade governed-agent-harness                 # PyPI
pipx upgrade governed-agent-harness                          # pipx
pip install --upgrade "governed-agent-harness @ <URL del wheel nuevo>"   # release de GitHub
docker pull ghcr.io/sebasbarrera/harness-engineering:<versión>
```

Después comprueba la versión (`harness --version`) y el proyecto (`harness doctor --path .`). Un `project.yaml` que no tiene una clave nueva conserva el comportamiento anterior; las claves nuevas se agregan a mano (o con `harness init --force` en un proyecto nuevo, que reemplaza el archivo) y se validan con `harness config validate`. Lee el [CHANGELOG](https://github.com/SebasBarrera/harness-engineering/blob/develop/CHANGELOG.md) antes de saltar varias versiones.

Las ejecuciones ya registradas siguen en el directorio de estado (ver [Estructura generada](estructura-generada.md)); actualizar el paquete no las toca.

## Desinstalar

```bash
pip uninstall governed-agent-harness
pipx uninstall governed-agent-harness
```

En la prueba, `pip uninstall -y governed-agent-harness` eliminó el paquete y el script `harness` del entorno. El resto queda, a propósito:

- `.harness/` en cada proyecto y la línea `.harness/` en `.git/info/exclude`.
- El directorio de estado con el historial de ejecuciones (`harness registry` lo muestra mientras esté instalado) y las anclas de la cadena. Bórralos a mano solo si no necesitas el historial; antes puedes exportar la evidencia con `harness export`.

## Comprobar la versión instalada

```bash
harness --version                       # harness 1.0.0
pip show governed-agent-harness         # nombre, versión, resumen y página
python -c "import governed_harness; print(governed_harness.__version__)"
harness doctor                          # la primera línea repite la versión
```

`harness --version` y `pip show` se ejecutaron en la prueba (`harness 1.0.0`, `Version: 1.0.0`); la línea de Python lee la misma constante `__version__` del código. Con Docker, `docker run --rm ghcr.io/sebasbarrera/harness-engineering:<versión> --version`.

## Desde el código fuente

```bash
git clone https://github.com/SebasBarrera/harness-engineering
cd harness-engineering
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,api]"
```
