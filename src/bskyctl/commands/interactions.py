from __future__ import annotations

import sys

from ..config import get_client
from ..ratelimit import call_with_write_backoff
from .postrefs import get_viewer_refs, resolve_post_ref


def cmd_like(args) -> None:
    client = get_client(profile=args.profile)
    try:
        uri, cid, public_url = resolve_post_ref(client, args.post)
        call_with_write_backoff(lambda: client.like(uri, cid))
        print(f"Liked: {public_url or uri}")
    except Exception as e:
        print(f"Like failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_unlike(args) -> None:
    client = get_client(profile=args.profile)
    try:
        uri, _cid, public_url = resolve_post_ref(client, args.post)
        like_uri, _repost_uri = get_viewer_refs(client, uri)
        if not like_uri:
            print("Not liked (nothing to undo).")
            return
        call_with_write_backoff(lambda: client.unlike(like_uri))
        print(f"Unliked: {public_url or uri}")
    except Exception as e:
        print(f"Unlike failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_repost(args) -> None:
    client = get_client(profile=args.profile)
    try:
        uri, cid, public_url = resolve_post_ref(client, args.post)
        call_with_write_backoff(lambda: client.repost(uri, cid))
        print(f"Reposted: {public_url or uri}")
    except Exception as e:
        print(f"Repost failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_unrepost(args) -> None:
    client = get_client(profile=args.profile)
    try:
        uri, _cid, public_url = resolve_post_ref(client, args.post)
        _like_uri, repost_uri = get_viewer_refs(client, uri)
        if not repost_uri:
            print("Not reposted (nothing to undo).")
            return
        call_with_write_backoff(lambda: client.unrepost(repost_uri))
        print(f"Unreposted: {public_url or uri}")
    except Exception as e:
        print(f"Unrepost failed: {e}", file=sys.stderr)
        raise SystemExit(1)
