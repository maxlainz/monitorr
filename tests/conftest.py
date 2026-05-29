import os
import tempfile
from collections.abc import AsyncIterator

import pytest_asyncio

# La app crea la BD en MONITORR_CONFIG_DIR al arrancar (lifespan). En tests apuntamos a un
# directorio temporal antes de importar la app, en vez de al /config por defecto.
os.environ.setdefault("MONITORR_CONFIG_DIR", tempfile.mkdtemp(prefix="monitorr-test-"))

from monitorr.config import get_settings  # noqa: E402
from monitorr.db import init_db  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def fresh_db() -> AsyncIterator[None]:
    path = get_settings().db_path
    for candidate in (path, path.with_suffix(".db-wal"), path.with_suffix(".db-shm")):
        candidate.unlink(missing_ok=True)
    await init_db(path)
    yield
