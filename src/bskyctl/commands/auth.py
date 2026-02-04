from __future__ import annotations

import sys

from ..atproto_compat import require_atproto
from ..config import load_config, resolve_profile, save_config


def cmd_login(args) -> None:
    Client, _client_utils, _models = require_atproto()

    cfg = load_config()
    profiles = cfg.get("profiles") or {}

    name = args.name or args.handle
    name = name.strip()

    if not name:
        print("Missing profile name. Use: bskyctl login --name <profile> ...", file=sys.stderr)
        raise SystemExit(1)

    try:
        client = Client()
        client.login(args.handle, args.password)

        profiles[name] = {
            "handle": args.handle,
            "app_password": args.password,
            "did": client.me.did,
        }
        cfg["profiles"] = profiles

        if args.set_active or not cfg.get("active"):
            cfg["active"] = name

        save_config(cfg)
        active_note = " (active)" if cfg.get("active") == name else ""
        print(f"Logged in profile '{name}' as {args.handle} ({client.me.did}){active_note}")
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_whoami(args) -> None:
    cfg = load_config()
    try:
        profile_name, _p = resolve_profile(cfg, profile=args.profile)
    except Exception:
        print("Not logged in")
        return

    from ..config import get_client

    client = get_client(profile=args.profile)
    print(f"Profile: {profile_name}")
    print(f"Handle: {client.me.handle}")
    print(f"DID: {client.me.did}")


def cmd_accounts(_args) -> None:
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    active = cfg.get("active")

    if not profiles:
        print(
            "No profiles configured. Use: bskyctl login --name <profile> "
            "--handle <handle> --password <app-password>"
        )
        return

    for name, p in profiles.items():
        star = "*" if name == active else " "
        handle = p.get("handle") or "(missing handle)"
        did = p.get("did") or "(no did)"
        print(f"{star} {name}: {handle}  {did}")


def cmd_use(args) -> None:
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    if args.name not in profiles:
        print(f"Unknown profile: {args.name}", file=sys.stderr)
        raise SystemExit(1)
    cfg["active"] = args.name
    save_config(cfg)
    print(f"Active profile set to '{args.name}'")


def cmd_logout(args) -> None:
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    if args.name not in profiles:
        print(f"Unknown profile: {args.name}", file=sys.stderr)
        raise SystemExit(1)

    del profiles[args.name]
    cfg["profiles"] = profiles

    if cfg.get("active") == args.name:
        cfg["active"] = next(iter(profiles.keys()), None)

    save_config(cfg)
    print(f"Removed profile '{args.name}'")
