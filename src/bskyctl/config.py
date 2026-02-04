from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .atproto_compat import require_atproto

CONFIG_PATH = Path.home() / ".config" / "bsky" / "config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {"profiles": {}, "active": None}
    else:
        cfg = {}

    # Migrate v1 → v2 in-memory.
    if "profiles" not in cfg:
        profiles = {}
        if cfg.get("handle") and cfg.get("app_password"):
            profiles["default"] = {
                "handle": cfg.get("handle"),
                "app_password": cfg.get("app_password"),
                "did": cfg.get("did"),
            }
            active = cfg.get("active") or "default"
        else:
            active = None
        cfg = {"profiles": profiles, "active": active}

    cfg.setdefault("profiles", {})
    cfg.setdefault("active", None)
    return cfg


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def resolve_profile(cfg: dict, *, profile: str | None) -> tuple[str, dict]:
    profiles = cfg.get("profiles") or {}

    # priority: explicit profile arg > env var > active
    profile_name = profile or os.getenv("BSKY_PROFILE") or cfg.get("active")

    if not profile_name:
        raise ValueError("No profile selected")

    if profile_name not in profiles:
        raise ValueError(f"Unknown profile: {profile_name}")

    return profile_name, profiles[profile_name]


def get_client(*, profile: str | None = None):
    Client, _client_utils, _models = require_atproto()

    cfg = load_config()
    try:
        profile_name, p = resolve_profile(cfg, profile=profile)
    except Exception:
        print(
            "Not logged in. Create a profile first:\n"
            "  bskyctl login --name <profile> --handle <handle> --password <app-password>\n"
            "Then select it:\n"
            "  bskyctl use <profile>\n"
            "Or run commands with:\n"
            "  bskyctl --profile <profile> <command>",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if not p.get("handle") or not p.get("app_password"):
        print(
            f"Profile '{profile_name}' is missing credentials. Re-run login for that profile.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    client = Client()
    client.login(p["handle"], p["app_password"])
    return client
