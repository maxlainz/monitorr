from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("monitorr")
except PackageNotFoundError:  # pragma: no cover - solo en árboles sin instalar
    __version__ = "0.0.0+dev"
