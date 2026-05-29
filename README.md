# monitorr

Servicio en Docker que observa qué series se están viendo en **Plex** y, vía API de
**Sonarr**, mantiene monitorizados/descargados *N* episodios por delante del punto de
visionado y conserva solo *N* por detrás (borrando el resto del disco a través de Sonarr),
protegiendo episodios clave como el piloto. Todo **solo vía API**, sin acceso al disco de
media.

> Estado: esqueleto del stack. La lógica de Plex/Sonarr/ventana aún no está implementada.

## Stack

Python 3.12 · FastAPI · HTMX (server-rendered) · SQLite · Docker single-image (amd64/arm64).
Detalle en [`.claude/tech-stack.md`](.claude/tech-stack.md).

## Desarrollo

```bash
uv sync
uv run monitorr            # Web UI en http://localhost:8080
```

Comandos de build/test/lint y despliegue: [`.claude/workflows.md`](.claude/workflows.md).
