"""Universe identity and backward-compatible saved-scan discovery."""
import json
from . import config

UNIVERSES = {"sp500": "S&P 500", "nasdaq100": "Nasdaq-100"}


def scan_directory(asof, universe):
    if universe not in UNIVERSES:
        raise ValueError(f"Unknown universe: {universe}")
    return config.SCANS / (asof.isoformat() if universe == "sp500" else f"{asof.isoformat()}_{universe}")


def available_scans(root, universe):
    scans = []
    for path in root.glob("*"):
        if not (path / "metrics.csv").exists():
            continue
        try:
            cfg = json.loads((path / "config.json").read_text())
        except (OSError, ValueError):
            continue
        if cfg.get("universe", "sp500") == universe:
            scans.append((cfg.get("asof", path.name[:10]), path))
    return [path for _, path in sorted(scans)]
