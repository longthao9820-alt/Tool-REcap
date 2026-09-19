from __future__ import annotations

import os
from pathlib import Path


def default_data_directory() -> Path:
    configured = os.environ.get("RECAP_STUDIO_DATA")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / "AppData" / "Local" / "RecapStudio"
