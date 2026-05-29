# Cómo y cuándo documentar

Norma de mantenimiento de la documentación de este repo. **La documentación es parte
del cambio, no un paso posterior.** Si un cambio invalida un doc, el doc se actualiza
**en el mismo commit**.

## Dónde vive qué

- **`CLAUDE.md`** (raíz) = portada: intro del proyecto, **Reglas** y la tabla **Contexto**
  que indexa todos los docs de `.claude/` con un "cuándo leer". No detalle técnico aquí.
- **`.claude/*.md`** = el detalle, **un archivo por dominio/concern** (no por capa).
  Ej.: `data-model.md`, `api.md`, `ui-features.md` — nunca `backend.md` / `frontend.md`.
- **Single source of truth**: cada hecho vive en un solo sitio. No dupliques una regla
  o un comando en dos docs; enlaza al doc canónico con un enlace relativo.

## Cuándo actualizar un doc existente

Actualiza el doc afectado **en el mismo commit** que el cambio, ante cualquiera de estos
disparadores:

- Cambio de arquitectura, componentes o flujo de datos → `architecture.md`.
- Stack, dependencias o estructura de directorios → `architecture.md` (o `tech-stack.md` si existe).
- Nuevo comando de desarrollo, build, test o deploy → `workflows.md`.
- Nueva regla de código, git, estilo o idioma → `rules.md`.
- Cambio en el esquema de datos o en una ruta/endpoint → el doc de ese dominio (`data-model.md`, `api.md`).
- Cambio en el flujo de git, releases o CI → `rules.md` / `workflows.md` / `versioning.md`.

## Cuándo crear un doc nuevo (vs. ampliar uno existente)

Crea un `.claude/<dominio>.md` nuevo cuando **aparece un dominio que aún no existe** y
empieza a tener reglas, formatos o pitfalls propios. Si solo añades un detalle a un
dominio ya cubierto, **amplía el doc existente** — no fragmentes.

Menú de ampliación previsto (crear cuando el código lo justifique):

| Doc candidato | Crear cuando… |
|---|---|
| `tech-stack.md` | Se fija el stack: versiones, layout, decisiones técnicas |
| `data-model.md` | Aparece persistencia: entidades, esquema, invariantes |
| `api.md` | Se expone una API: rutas, contratos, auth, formato de respuesta |
| `ui-features.md` | Hay UI: catálogo de páginas, acciones, qué se configura por web vs env |
| `versioning.md` | Se cortan releases: SemVer, conventional commits, bump, tags |
| `<feature>.md` | Una feature tiene lógica/pitfalls suficientes para merecer su propia página |

## Al añadir un doc nuevo

1. Crea el archivo en `.claude/` con el formato de abajo.
2. **Registra su fila** en la tabla *Contexto* de `CLAUDE.md` con un "cuándo leer" claro.
3. Si reemplaza contenido que estaba en otro doc, muévelo y deja solo un enlace.

## Formato de cada doc

- Título `#` en la primera línea; secciones con `##` por tema.
- Enlaces entre docs en formato relativo markdown: `` [`x.md`](x.md) `` (y `../` para
  archivos fuera de `.claude/`, p. ej. `` [`ci.yml`](../.github/workflows/ci.yml) ``).
- **Explicar el porqué**, no el qué: invariantes, trade-offs, pitfalls, restricciones
  externas. Evitar narrar lo que el código ya dice.
- Compacto y directivo. Sin relleno.

## Idioma y estilo

- Documentación y mensajes de commit: **español**.
- Código, identificadores, nombres de archivo, ramas y env vars: **inglés**.
- Estilo de doc espejo del de `rules.md`: frases cortas, listas, tablas para índices.
