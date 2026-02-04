from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..config import get_client
from ..ratelimit import call_with_read_backoff
from ..utils import atomic_write_lines, normalize_handle


@dataclass
class _Page:
    items: list
    cursor: str | None


def _fetch_followers(client, actor: str, *, limit: int, cursor: str | None) -> _Page:
    params: dict = {"actor": actor, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    resp = call_with_read_backoff(lambda: client.app.bsky.graph.get_followers(params))
    return _Page(items=getattr(resp, "followers", []) or [], cursor=getattr(resp, "cursor", None))


def _fetch_follows(client, actor: str, *, limit: int, cursor: str | None) -> _Page:
    params: dict = {"actor": actor, "limit": limit}
    if cursor:
        params["cursor"] = cursor
    resp = call_with_read_backoff(lambda: client.app.bsky.graph.get_follows(params))
    return _Page(items=getattr(resp, "follows", []) or [], cursor=getattr(resp, "cursor", None))


def _format_actor(item, mode: str) -> str:
    handle = getattr(item, "handle", None)
    did = getattr(item, "did", None)

    if mode == "did":
        return did or handle or ""
    if mode == "handle+did":
        if handle and did:
            return f"{handle}\t{did}"
        return handle or did or ""
    # default: handle
    return handle or did or ""


def _collect_paged(
    fetch_page,
    *,
    progress_prefix: str,
    actor: str,
    limit: int,
    mode: str,
    progress_every: int,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    cursor: str | None = None
    n = 0
    while True:
        page = fetch_page(actor=actor, limit=limit, cursor=cursor)
        for item in page.items:
            line = _format_actor(item, mode).strip()
            if not line or line in seen:
                continue
            seen.add(line)
            out.append(line)
            n += 1
            if progress_every > 0 and n % progress_every == 0:
                print(f"{progress_prefix}: {n} ...")

        cursor = page.cursor
        if not cursor:
            break

    return out


def cmd_graph_export(args) -> None:
    """Export followers and/or follows to a text file."""

    client = get_client(profile=args.profile)

    actor_raw: str = args.actor
    actor = actor_raw.strip()
    if not actor.startswith("did:"):
        actor = normalize_handle(actor)

    limit = int(args.limit)
    if limit <= 0:
        raise SystemExit("--limit must be > 0")

    mode = args.format
    only = args.only

    exported_at = datetime.now(timezone.utc).isoformat()

    followers: list[str] = []
    follows: list[str] = []

    if only in ("followers", "both"):
        print(f"Exporting followers for {actor} ...")
        followers = _collect_paged(
            lambda **kw: _fetch_followers(client, **kw),
            progress_prefix="followers",
            actor=actor,
            limit=limit,
            mode=mode,
            progress_every=int(args.progress_every),
        )

    if only in ("follows", "both"):
        print(f"Exporting follows for {actor} ...")
        follows = _collect_paged(
            lambda **kw: _fetch_follows(client, **kw),
            progress_prefix="follows",
            actor=actor,
            limit=limit,
            mode=mode,
            progress_every=int(args.progress_every),
        )

    lines: list[str] = []
    lines.append("# bskyctl graph export")
    lines.append(f"# actor: {actor}")
    lines.append(f"# exportedAt: {exported_at}")
    lines.append(f"# format: {mode}")
    lines.append("")

    if only in ("followers", "both"):
        lines.append("[followers]")
        lines.extend(followers)
        lines.append("")

    if only in ("follows", "both"):
        lines.append("[follows]")
        lines.extend(follows)
        lines.append("")

    atomic_write_lines(args.out, lines)

    parts: list[str] = []
    if only in ("followers", "both"):
        parts.append(f"followers={len(followers)}")
    if only in ("follows", "both"):
        parts.append(f"follows={len(follows)}")
    parts.append(f"out={args.out}")
    print("Done. " + " ".join(parts))


def cmd_graph(args) -> None:
    if getattr(args, "graph_command", None) == "export":
        return cmd_graph_export(args)

    raise SystemExit("Missing graph subcommand. Try: bskyctl graph export --help")
