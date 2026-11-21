"""OTW read-only AI investment research. Never routes orders."""
import os
import re

__version__ = "0.8.0"


def release_metadata() -> dict[str, str | None]:
    sha = os.getenv("ENGINE_GIT_SHA", "")
    return {
        "version": __version__,
        "git_sha": sha.lower() if re.fullmatch(r"[0-9a-fA-F]{40}", sha) else None,
    }
