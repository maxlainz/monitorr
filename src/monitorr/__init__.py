from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("monitorr")
except PackageNotFoundError:  # pragma: no cover - only in uninstalled trees
    __version__ = "0.0.0+dev"
