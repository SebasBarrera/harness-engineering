# Instalación

Guía básica para instalar el harness y comprobar que funciona. Para pipx, extras, una versión concreta, verificación de la firma, instalación sin red, actualización y desinstalación, ver [Instalación avanzada](instalacion-avanzada.md).

## Requisitos

- Python 3.12 o superior.
- Git: el harness calcula los cambios contra el repositorio.
- Para proyectos Node.js, Node.js LTS y npm.

El harness es un paquete de Python. No se instala con npm ni con npx.

## Elige un canal

| Canal | Cuándo usarlo | Comando |
|---|---|---|
| PyPI | Lo más simple, si el mantenedor ya habilitó la publicación. | `pip install governed-agent-harness` |
| Wheel del release de GitHub | Una versión concreta, verificable con suma de comprobación y atestación. | `pip install "governed-agent-harness @ <URL del wheel>"` |
| Docker (GHCR) | No quieres instalar Python en tu equipo. | `docker run --rm ghcr.io/sebasbarrera/harness-engineering:<versión> --help` |
| Código fuente | Vas a modificar el harness. | `pip install -e ".[dev,api]"` dentro del clon |

La publicación en PyPI se activa con una variable del repositorio y una configuración que hace el mantenedor una sola vez (ver [Publicación](publicacion.md)). Hasta entonces `pip install governed-agent-harness` no encuentra el paquete; usa el wheel del release. No pude comprobar desde aquí si el paquete ya existe en PyPI.

## Desde PyPI

Dentro de un entorno virtual:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install governed-agent-harness
harness --version
```

## Desde el wheel del release

Cada versión publica en [GitHub Releases](https://github.com/SebasBarrera/harness-engineering/releases) el wheel, el sdist, `SHA256SUMS` y un SBOM. Instala el wheel directamente desde su URL:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install "governed-agent-harness @ https://github.com/SebasBarrera/harness-engineering/releases/download/v1.0.0/governed_agent_harness-1.0.0-py3-none-any.whl"
harness --version
```

Cambia `v1.0.0` y `1.0.0` por la versión que quieras. Para verificar el archivo antes de instalarlo, ver [Instalación avanzada](instalacion-avanzada.md#verificar-las-sumas-y-la-atestacion).

## Con Docker

La imagen se publica en GHCR con cada versión, para `linux/amd64` y `linux/arm64`, y corre con un usuario sin privilegios (uid 10001):

```bash
docker run --rm ghcr.io/sebasbarrera/harness-engineering:1.0.0 --help
```

Para trabajar sobre un proyecto, monta el repositorio en `/workspace`, que es el directorio de trabajo de la imagen:

```bash
docker run --rm -v "$PWD":/workspace ghcr.io/sebasbarrera/harness-engineering:1.0.0 doctor --path /workspace
```

No pude ejecutar los comandos de Docker de esta página (sin red en el entorno de prueba). Lo que sí comprueba el flujo `docker.yml` en cada push es que la imagen responde a `--help` y a `doctor` y que corre con el uid 10001. Dos cuidados esperables con el montaje: el usuario de la imagen debe poder escribir en el directorio montado, y Git puede quejarse de «dubious ownership» si el propietario del montaje es otro usuario.

## Comprobar la instalación

```bash
harness --version
harness doctor
```

`harness --version` imprime la versión (en la prueba, `harness 1.0.0`). `harness doctor` revisa Python, Git, Node.js y npm; fuera de un proyecto solo valida el entorno, y con `--path` también valida el proyecto.

## Primeros pasos

1. Entra a un repositorio de Git con al menos un commit.
2. `harness init` crea `.harness/project.yaml` y una tarea de ejemplo.
3. Sigue [Primeros pasos](primeros-pasos.md) para tu primera ejecución, o las guías de [proyecto nuevo](proyecto-nuevo.md) y [proyecto existente](proyecto-existente.md).

Si quieres el tablero web local, instala también el extra `api`:

```bash
pip install "governed-agent-harness[api]"
```
