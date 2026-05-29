import os
import tempfile

# La app crea la BD en MONITORR_CONFIG_DIR al arrancar (lifespan). En tests apuntamos a un
# directorio temporal antes de importar la app, en vez de al /config por defecto.
os.environ.setdefault("MONITORR_CONFIG_DIR", tempfile.mkdtemp(prefix="monitorr-test-"))
