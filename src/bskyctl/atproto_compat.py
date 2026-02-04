"""Internal helper to import atproto with a friendly error.

We keep the dependency light and import lazily so that `bskyctl --help` works
without requiring atproto to be installed yet.
"""

from __future__ import annotations

import sys


def require_atproto():
    """Return (Client, client_utils, models) or exit with a helpful message."""

    try:
        from atproto import Client, client_utils, models  # type: ignore

        return Client, client_utils, models
    except Exception:
        print(
            "Error: atproto not installed. Install it first (e.g. `pipx install bskyctl`\n"
            "or `python -m pip install atproto`).",
            file=sys.stderr,
        )
        raise SystemExit(1)
