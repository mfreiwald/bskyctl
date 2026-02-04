#!/usr/bin/env python3
"""Bluesky CLI - bird-like interface for Bluesky/AT Protocol"""

# /// script
# dependencies = ["atproto>=0.0.0"]
# ///

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import fcntl  # unix-only; used for cross-process throttling
except Exception:  # pragma: no cover
    fcntl = None

from atproto import models

try:
    from atproto import Client, client_utils
except ImportError:
    print(
        "Error: atproto not installed. Install via uv once: uv pip install atproto (or just run via: uv run bsky.py ...)" ,
        file=sys.stderr,
    )
    sys.exit(1)

CONFIG_PATH = Path.home() / ".config" / "bsky" / "config.json"
CACHE_DIR = Path.home() / ".cache" / "bsky"
RATE_STATE_DIR = CACHE_DIR / "ratelimit"

# Client-side throttling (helps when running multiple CLI calls in parallel).
# Defaults are conservative vs the hosted PDS limit (3000 requests / 5 minutes).
THROTTLE_ENABLED = True
REQ_RPS = float(os.getenv("BSKY_REQ_RPS", "8"))  # tokens/sec
REQ_BURST = float(os.getenv("BSKY_REQ_BURST", "16"))  # max accumulated tokens

# Config schema (v2):
# {
#   "active": "work",
#   "profiles": {
#     "work": {"handle": "...", "app_password": "...", "did": "..."},
#     "personal": {"handle": "...", "app_password": "...", "did": "..."}
#   }
# }
# Backward-compat (v1): {"handle": "...", "app_password": "...", "did": "..."}

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


def get_client(*, profile: str | None = None) -> Client:
    cfg = load_config()
    try:
        profile_name, p = resolve_profile(cfg, profile=profile)
    except Exception:
        print(
            "Not logged in. Create a profile first:\n"
            "  bsky login --name <profile> --handle <handle> --password <app-password>\n"
            "Then select it:\n"
            "  bsky use <profile>\n"
            "Or run commands with:\n"
            "  bsky --profile <profile> <command>",
            file=sys.stderr,
        )
        sys.exit(1)

    if not p.get("handle") or not p.get("app_password"):
        print(
            f"Profile '{profile_name}' is missing credentials. Re-run login for that profile.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = Client()
    client.login(p["handle"], p["app_password"])
    return client


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


class _SharedTokenBucket:
    """A tiny cross-process token bucket (best effort).

    Purpose: avoid smashing hosted PDS API limits when running multiple CLI
    invocations in parallel (e.g. many `bsky search ...` calls).

    Uses flock when available; otherwise falls back to per-process throttling.
    """

    def __init__(self, *, key: str, refill_per_s: float, capacity: float):
        self.key = key
        self.refill_per_s = max(0.001, float(refill_per_s))
        self.capacity = max(1.0, float(capacity))
        RATE_STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.state_path = RATE_STATE_DIR / f"{key}.json"
        self.lock_path = RATE_STATE_DIR / f"{key}.lock"
        self._local_tokens = self.capacity
        self._local_updated = time.time()

    def acquire(self, tokens: float = 1.0) -> None:
        if tokens <= 0:
            return

        # No flock? -> simple local token bucket.
        if fcntl is None:
            self._acquire_local(tokens)
            return

        while True:
            now = time.time()
            with self.lock_path.open("a+") as lockf:
                fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
                state = {"tokens": self.capacity, "updated": now}
                if self.state_path.exists():
                    try:
                        state = json.loads(self.state_path.read_text(encoding="utf-8"))
                    except Exception:
                        state = {"tokens": self.capacity, "updated": now}

                prev_tokens = float(state.get("tokens", self.capacity))
                prev_updated = float(state.get("updated", now))
                dt = max(0.0, now - prev_updated)
                avail = min(self.capacity, prev_tokens + dt * self.refill_per_s)

                if avail >= tokens:
                    new_state = {"tokens": avail - tokens, "updated": now}
                    _atomic_write_json(self.state_path, new_state)
                    fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
                    return

                # Not enough tokens yet.
                needed = tokens - avail
                wait_s = needed / self.refill_per_s
                new_state = {"tokens": avail, "updated": now}
                _atomic_write_json(self.state_path, new_state)
                fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

            time.sleep(min(2.0, max(0.01, wait_s)))

    def _acquire_local(self, tokens: float) -> None:
        while True:
            now = time.time()
            dt = max(0.0, now - self._local_updated)
            self._local_tokens = min(self.capacity, self._local_tokens + dt * self.refill_per_s)
            self._local_updated = now
            if self._local_tokens >= tokens:
                self._local_tokens -= tokens
                return
            wait_s = (tokens - self._local_tokens) / self.refill_per_s
            time.sleep(min(2.0, max(0.01, wait_s)))


_REQ_BUCKET: _SharedTokenBucket | None = None


def _get_req_bucket() -> _SharedTokenBucket:
    global _REQ_BUCKET
    if _REQ_BUCKET is None:
        _REQ_BUCKET = _SharedTokenBucket(key="req", refill_per_s=REQ_RPS, capacity=REQ_BURST)
    return _REQ_BUCKET


def _throttle_req(tokens: float = 1.0) -> None:
    if not THROTTLE_ENABLED:
        return
    _get_req_bucket().acquire(tokens)


def _call_with_read_backoff(fn, *, attempts: int = 3):
    """Generic wrapper for read-ish calls: throttle + retry on 429."""
    _throttle_req(1.0)
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if _is_rate_limited(e) and attempt < max(0, int(attempts) - 1):
                wait_s = random.uniform(2.0, 5.0) * (2 ** attempt)
                time.sleep(wait_s)
                attempt += 1
                continue
            raise


def _call_with_write_backoff(fn, *, attempts: int = 3):
    """Generic wrapper for write-ish calls: throttle + longer retry on 429."""
    _throttle_req(1.0)
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if _is_rate_limited(e) and attempt < max(0, int(attempts) - 1):
                wait_s = random.uniform(15.0, 35.0) * (1.6 ** attempt)
                print(f"Rate limited; backing off {wait_s:.1f}s ...", file=sys.stderr)
                time.sleep(wait_s)
                attempt += 1
                continue
            raise


def cmd_login(args):
    cfg = load_config()
    profiles = cfg.get("profiles") or {}

    name = args.name or args.handle
    name = name.strip()

    if not name:
        print("Missing profile name. Use: bsky login --name <profile> ...", file=sys.stderr)
        sys.exit(1)

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
        sys.exit(1)

def cmd_whoami(args):
    cfg = load_config()
    try:
        profile_name, _ = resolve_profile(cfg, profile=args.profile)
    except Exception:
        print("Not logged in")
        return

    client = get_client(profile=args.profile)
    print(f"Profile: {profile_name}")
    print(f"Handle: {client.me.handle}")
    print(f"DID: {client.me.did}")

def cmd_timeline(args):
    client = get_client(profile=args.profile)
    response = _call_with_read_backoff(lambda: client.get_timeline(limit=args.count))

    for item in response.feed:
        post = item.post
        author = post.author.handle
        text = post.record.text if hasattr(post.record, 'text') else ""
        created = post.record.created_at if hasattr(post.record, 'created_at') else ""
        likes = post.like_count or 0
        reposts = post.repost_count or 0
        replies = post.reply_count or 0
        
        try:
            dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            time_str = dt.strftime("%b %d %H:%M")
        except:
            time_str = created[:16] if created else ""
        
        print(f"@{author} · {time_str}")
        print(f"  {text[:200]}")
        print(f"  ❤️ {likes}  🔁 {reposts}  💬 {replies}")
        print(f"  🔗 https://bsky.app/profile/{author}/post/{post.uri.split('/')[-1]}")
        print()

def cmd_post(args):
    client = get_client(profile=args.profile)
    text = args.text

    # Bluesky clients do NOT auto-detect hashtags/mentions reliably.
    # To make #hashtags clickable/searchable we must emit proper facets.
    url_pattern = r"https?://[^\s]+"
    tag_pattern = r"#[A-Za-z0-9_]+"
    mention_pattern = r"@[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)*"

    token_re = re.compile(rf"({url_pattern}|{tag_pattern}|{mention_pattern})")
    matches = list(token_re.finditer(text))

    if not matches:
        response = _call_with_write_backoff(lambda: client.send_post(text=text))
    else:
        builder = client_utils.TextBuilder()
        last_end = 0

        for match in matches:
            if match.start() > last_end:
                builder.text(text[last_end:match.start()])

            token = match.group(1)

            if re.fullmatch(url_pattern, token):
                builder.link(token, token)
            elif re.fullmatch(tag_pattern, token):
                # token includes leading '#'
                builder.tag(token, token[1:])
            elif token.startswith("@"):
                # Mentions require a DID. Best effort: resolve handle.
                handle = _normalize_handle(token)
                try:
                    did = _call_with_read_backoff(lambda: client.resolve_handle(handle)).did
                    builder.mention(token, did)
                except Exception:
                    # Fallback to plain text if resolution fails.
                    builder.text(token)
            else:
                builder.text(token)

            last_end = match.end()

        if last_end < len(text):
            builder.text(text[last_end:])

        response = _call_with_write_backoff(lambda: client.send_post(builder))

    uri = response.uri
    post_id = uri.split("/")[-1]
    print(f"Posted: https://bsky.app/profile/{client.me.handle}/post/{post_id}")

def _resolve_post_ref(client: Client, value: str) -> tuple[str, str, str | None]:
    """Resolve a post reference.

    Returns: (uri, cid, public_url)

    Accepts:
    - bsky.app post URL
    - at://... uri (best effort)
    """
    value = value.strip()

    # URL form: https://bsky.app/profile/<handle>/post/<rkey>
    m = re.search(r"bsky\.app/profile/([^/]+)/post/([^/?#]+)", value)
    if m:
        handle = m.group(1)
        rkey = m.group(2)
        # Resolve handle -> DID
        did = _call_with_read_backoff(lambda: client.resolve_handle(handle)).did
        uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        posts = _call_with_read_backoff(lambda: client.get_posts([uri])).posts
        if not posts:
            raise RuntimeError(
                "Could not resolve post. Tip: paste the ORIGINAL post URL (author handle + post id)."
            )
        post = posts[0]
        public_url = f"https://bsky.app/profile/{handle}/post/{rkey}"
        return post.uri, post.cid, public_url

    # at://... uri
    if value.startswith("at://"):
        uri = value
        posts = _call_with_read_backoff(lambda: client.get_posts([uri])).posts
        if not posts:
            raise RuntimeError("Could not resolve post")
        post = posts[0]
        return post.uri, post.cid, None

    raise RuntimeError("Unsupported post reference (use a bsky.app post URL)")


def _normalize_handle(value: str) -> str:
    value = value.strip()
    if value.startswith("@"):
        value = value[1:]
    # If no domain is provided, assume .bsky.social
    if value and "." not in value and not value.startswith("did:"):
        value = f"{value}.bsky.social"
    return value


def _read_actor_lines(path: str) -> list[str]:
    p = Path(path).expanduser()
    if not p.exists():
        raise RuntimeError(f"List file not found: {p}")

    out: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        # allow inline comments
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if not line:
            continue
        out.append(line)

    # de-dupe while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for a in out:
        if a in seen:
            continue
        seen.add(a)
        uniq.append(a)

    return uniq


def _sleep_between(min_delay: float, max_delay: float, buffer: float) -> None:
    min_d = max(0.0, float(min_delay)) * (1.0 + float(buffer))
    max_d = max(min_d, float(max_delay)) * (1.0 + float(buffer))
    if max_d <= 0:
        return
    time.sleep(random.uniform(min_d, max_d))


def _append_line(path: str | None, line: str) -> None:
    if not path:
        return
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(line.rstrip("\n") + "\n")


def _atomic_write_lines(path: str, lines: list[str]) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, p)


def _rewrite_list_file(path: str, remaining: list[str]) -> None:
    _atomic_write_lines(path, remaining)


def _is_rate_limited(err: Exception) -> bool:
    msg = str(err)
    return (
        "429" in msg
        or "RateLimit" in msg
        or "rate limit" in msg.lower()
        or "TooManyRequests" in msg
        or "ratelimit" in msg.lower()
    )


def _is_already_exists(err: Exception) -> bool:
    msg = str(err)
    return (
        "AlreadyExists" in msg
        or "already exists" in msg.lower()
        or "Duplicate" in msg
        or "DuplicateRecord" in msg
        or "RecordAlreadyExists" in msg
    )


def _get_viewer_refs(client: Client, uri: str) -> tuple[str | None, str | None]:
    """Return (like_uri, repost_uri) for the authenticated viewer, if present."""
    posts = _call_with_read_backoff(lambda: client.get_posts([uri])).posts
    if not posts:
        return None, None
    post = posts[0]
    viewer = getattr(post, "viewer", None)
    if not viewer:
        return None, None
    like_uri = getattr(viewer, "like", None)
    repost_uri = getattr(viewer, "repost", None)
    return like_uri, repost_uri


def cmd_follow(args):
    client = get_client(profile=args.profile)

    try:
        actors: list[str]
        list_path = getattr(args, "list", None)
        if list_path:
            actors = _read_actor_lines(list_path)
        elif args.actor:
            actors = [args.actor]
        else:
            raise RuntimeError("Missing actor. Provide a handle/DID or use --list <file>.")

        max_n = args.max if getattr(args, "max", None) else None
        if max_n is not None:
            actors = actors[: int(max_n)]

        # Normalize + de-dupe AFTER normalization (so '@x' and 'x' collapse).
        seen: set[str] = set()
        norm_actors: list[str] = []
        for raw in actors:
            a = _normalize_handle(raw)
            if a in seen:
                continue
            seen.add(a)
            norm_actors.append(a)
        actors = norm_actors

        # cache handle -> did to cut resolve requests
        did_cache: dict[str, str] = {}

        ok: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        remaining = actors.copy()

        def checkpoint() -> None:
            if args.inplace and list_path:
                _rewrite_list_file(list_path, remaining)
            _atomic_write_lines(args.out_remaining, remaining) if args.out_remaining else None

        try:
            processed = 0
            while remaining:
                # Keep the current actor in `remaining` until it is successfully handled,
                # so an abrupt abort leaves the list file pointing at the true remainder.
                actor = remaining[0]
                idx = processed + 1

                if args.dry_run:
                    print(f"DRY RUN follow: {actor}")
                    ok.append(actor)
                    _append_line(args.out_followed, actor)
                    remaining.pop(0)
                    processed += 1
                    checkpoint()
                    _sleep_between(args.min_delay, args.max_delay, args.buffer)
                    continue

                attempt = 0
                handled = False
                while True:
                    try:
                        if actor.startswith("did:"):
                            did = actor
                        else:
                            did = did_cache.get(actor)
                            if not did:
                                _throttle_req(1.0)
                                did = client.resolve_handle(actor).did
                                did_cache[actor] = did

                        _throttle_req(1.0)
                        client.follow(did)
                        print(f"Followed ({idx}/{len(actors)}): {actor}")
                        ok.append(actor)
                        _append_line(args.out_followed, actor)
                        # success => remove from queue
                        remaining.pop(0)
                        processed += 1
                        handled = True
                        break
                    except Exception as e:
                        if _is_already_exists(e):
                            print(f"Already following ({idx}/{len(actors)}): {actor}")
                            skipped.append(actor)
                            _append_line(args.out_skipped, actor)
                            remaining.pop(0)
                            processed += 1
                            handled = True
                            break

                        if _is_rate_limited(e) and attempt < 2:
                            wait_s = random.uniform(20, 40) * (1.0 + float(args.buffer))
                            print(f"Rate limited; backing off {wait_s:.1f}s ...", file=sys.stderr)
                            time.sleep(wait_s)
                            attempt += 1
                            continue

                        msg = str(e)
                        print(f"Follow failed ({idx}/{len(actors)}): {actor} :: {msg}", file=sys.stderr)
                        failed.append(actor)
                        _append_line(args.out_failed, actor)
                        # failure => move to end (so we still progress)
                        remaining.pop(0)
                        remaining.append(actor)
                        processed += 1
                        handled = True
                        break

                if handled:
                    checkpoint()
                _sleep_between(args.min_delay, args.max_delay, args.buffer)

        except KeyboardInterrupt:
            print("Interrupted. Writing remaining list for resume...", file=sys.stderr)
            checkpoint()
            raise

        if args.rewrite_input and list_path and not args.inplace:
            # Keep only failures by default so a rerun targets what didn't work.
            _rewrite_list_file(list_path, failed)
        elif args.rewrite_input and args.inplace:
            print("Note: --rewrite-input ignored when --inplace is set.", file=sys.stderr)

        print(f"Done. followed={len(ok)} skipped={len(skipped)} failed={len(failed)}")
    except Exception as e:
        print(f"Follow failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_unfollow(args):
    client = get_client(profile=args.profile)

    try:
        actors: list[str]
        list_path = getattr(args, "list", None)
        if list_path:
            actors = _read_actor_lines(list_path)
        elif args.actor:
            actors = [args.actor]
        else:
            raise RuntimeError("Missing actor. Provide a handle/DID or use --list <file>.")

        max_n = args.max if getattr(args, "max", None) else None
        if max_n is not None:
            actors = actors[: int(max_n)]

        # Normalize + de-dupe AFTER normalization (so '@x' and 'x' collapse).
        seen: set[str] = set()
        norm_actors: list[str] = []
        for raw in actors:
            a = _normalize_handle(raw)
            if a in seen:
                continue
            seen.add(a)
            norm_actors.append(a)
        actors = norm_actors

        ok: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        remaining = actors.copy()

        def checkpoint() -> None:
            if args.inplace and list_path:
                _rewrite_list_file(list_path, remaining)
            _atomic_write_lines(args.out_remaining, remaining) if args.out_remaining else None

        try:
            processed = 0
            while remaining:
                actor = remaining[0]
                idx = processed + 1

                if args.dry_run:
                    print(f"DRY RUN unfollow: {actor}")
                    ok.append(actor)
                    _append_line(args.out_unfollowed, actor)
                    remaining.pop(0)
                    processed += 1
                    checkpoint()
                    _sleep_between(args.min_delay, args.max_delay, args.buffer)
                    continue

                attempt = 0
                handled = False
                while True:
                    try:
                        _throttle_req(1.0)
                        profile = client.get_profile(actor)
                        viewer = getattr(profile, "viewer", None)
                        follow_uri = getattr(viewer, "following", None) if viewer else None
                        if not follow_uri:
                            print(f"Not following ({idx}/{len(actors)}): {actor}")
                            skipped.append(actor)
                            _append_line(args.out_skipped, actor)
                        else:
                            _throttle_req(1.0)
                            client.unfollow(follow_uri)
                            print(f"Unfollowed ({idx}/{len(actors)}): {actor}")
                            ok.append(actor)
                            _append_line(args.out_unfollowed, actor)

                        remaining.pop(0)
                        processed += 1
                        handled = True
                        break
                    except Exception as e:
                        if _is_rate_limited(e) and attempt < 2:
                            wait_s = random.uniform(20, 40) * (1.0 + float(args.buffer))
                            print(f"Rate limited; backing off {wait_s:.1f}s ...", file=sys.stderr)
                            time.sleep(wait_s)
                            attempt += 1
                            continue
                        msg = str(e)
                        print(f"Unfollow failed ({idx}/{len(actors)}): {actor} :: {msg}", file=sys.stderr)
                        failed.append(actor)
                        _append_line(args.out_failed, actor)
                        remaining.pop(0)
                        remaining.append(actor)
                        processed += 1
                        handled = True
                        break

                if handled:
                    checkpoint()
                _sleep_between(args.min_delay, args.max_delay, args.buffer)

        except KeyboardInterrupt:
            print("Interrupted. Writing remaining list for resume...", file=sys.stderr)
            checkpoint()
            raise

        if args.rewrite_input and list_path and not args.inplace:
            _rewrite_list_file(list_path, failed)
        elif args.rewrite_input and args.inplace:
            print("Note: --rewrite-input ignored when --inplace is set.", file=sys.stderr)

        print(f"Done. unfollowed={len(ok)} skipped={len(skipped)} failed={len(failed)}")
    except Exception as e:
        print(f"Unfollow failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_like(args):
    client = get_client(profile=args.profile)
    try:
        uri, cid, public_url = _resolve_post_ref(client, args.post)
        _call_with_write_backoff(lambda: client.like(uri, cid))
        print(f"Liked: {public_url or uri}")
    except Exception as e:
        print(f"Like failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_unlike(args):
    client = get_client(profile=args.profile)
    try:
        uri, _cid, public_url = _resolve_post_ref(client, args.post)
        like_uri, _repost_uri = _get_viewer_refs(client, uri)
        if not like_uri:
            print("Not liked (nothing to undo).")
            return
        _call_with_write_backoff(lambda: client.unlike(like_uri))
        print(f"Unliked: {public_url or uri}")
    except Exception as e:
        print(f"Unlike failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_repost(args):
    client = get_client(profile=args.profile)
    try:
        uri, cid, public_url = _resolve_post_ref(client, args.post)
        _call_with_write_backoff(lambda: client.repost(uri, cid))
        print(f"Reposted: {public_url or uri}")
    except Exception as e:
        print(f"Repost failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_unrepost(args):
    client = get_client(profile=args.profile)
    try:
        uri, _cid, public_url = _resolve_post_ref(client, args.post)
        _like_uri, repost_uri = _get_viewer_refs(client, uri)
        if not repost_uri:
            print("Not reposted (nothing to undo).")
            return
        _call_with_write_backoff(lambda: client.unrepost(repost_uri))
        print(f"Unreposted: {public_url or uri}")
    except Exception as e:
        print(f"Unrepost failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_quote(args):
    client = get_client(profile=args.profile)
    try:
        uri, cid, public_url = _resolve_post_ref(client, args.post)
        embed = models.AppBskyEmbedRecord.Main(record=models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid))

        # Reuse the same rich-text builder behavior as cmd_post (links + hashtags + mentions facets)
        text = args.text
        url_pattern = r"https?://[^\s]+"
        tag_pattern = r"#[A-Za-z0-9_]+"
        mention_pattern = r"@[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)*"
        token_re = re.compile(rf"({url_pattern}|{tag_pattern}|{mention_pattern})")
        matches = list(token_re.finditer(text))

        if not matches:
            response = _call_with_write_backoff(lambda: client.send_post(text=text, embed=embed))
        else:
            builder = client_utils.TextBuilder()
            last_end = 0
            for match in matches:
                if match.start() > last_end:
                    builder.text(text[last_end:match.start()])

                token = match.group(1)
                if re.fullmatch(url_pattern, token):
                    builder.link(token, token)
                elif re.fullmatch(tag_pattern, token):
                    builder.tag(token, token[1:])
                elif token.startswith("@"):
                    handle = _normalize_handle(token)
                    try:
                        did = _call_with_read_backoff(lambda: client.resolve_handle(handle)).did
                        builder.mention(token, did)
                    except Exception:
                        builder.text(token)
                else:
                    builder.text(token)
                last_end = match.end()
            if last_end < len(text):
                builder.text(text[last_end:])

            response = _call_with_write_backoff(lambda: client.send_post(builder, embed=embed))

        post_id = response.uri.split("/")[-1]
        print(f"Quoted: https://bsky.app/profile/{client.me.handle}/post/{post_id}")
        if public_url:
            print(f"  ↳ original: {public_url}")
    except Exception as e:
        print(f"Quote failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_delete(args):
    client = get_client(profile=args.profile)
    # Extract post ID from URL or use raw ID
    post_id = args.post_id
    if "bsky.app" in post_id:
        post_id = post_id.rstrip('/').split('/')[-1]
    
    # Construct the URI
    uri = f"at://{client.me.did}/app.bsky.feed.post/{post_id}"
    
    try:
        _call_with_write_backoff(lambda: client.delete_post(uri))
        print(f"Deleted post: {post_id}")
    except Exception as e:
        print(f"Delete failed: {e}", file=sys.stderr)
        sys.exit(1)

def cmd_profile(args):
    client = get_client(profile=args.profile)
    handle = args.handle.lstrip('@') if args.handle else client.me.handle
    
    # Auto-append .bsky.social if no domain specified
    if handle and '.' not in handle:
        handle = f"{handle}.bsky.social"
    
    profile = _call_with_read_backoff(lambda: client.get_profile(handle))
    print(f"@{profile.handle}")
    print(f"  Name: {profile.display_name or '(none)'}")
    print(f"  Bio: {profile.description or '(none)'}")
    print(f"  Followers: {profile.followers_count}")
    print(f"  Following: {profile.follows_count}")
    print(f"  Posts: {profile.posts_count}")
    print(f"  DID: {profile.did}")

def cmd_search(args):
    client = get_client(profile=args.profile)
    response = _call_with_read_backoff(
        lambda: client.app.bsky.feed.search_posts({"q": args.query, "limit": args.count})
    )
    
    if not response.posts:
        print("No results found.")
        return
    
    for post in response.posts:
        author = post.author.handle
        text = post.record.text if hasattr(post.record, 'text') else ""
        likes = post.like_count or 0
        
        print(f"@{author}: {text[:150]}")
        print(f"  ❤️ {likes}  🔗 https://bsky.app/profile/{author}/post/{post.uri.split('/')[-1]}")
        print()

def cmd_notifications(args):
    client = get_client(profile=args.profile)
    response = _call_with_read_backoff(
        lambda: client.app.bsky.notification.list_notifications({"limit": args.count})
    )
    
    for notif in response.notifications:
        reason = notif.reason
        author = notif.author.handle
        time_str = notif.indexed_at[:16] if notif.indexed_at else ""
        
        icons = {
            "like": "❤️",
            "repost": "🔁",
            "follow": "👤",
            "reply": "💬",
            "mention": "📢",
            "quote": "💭"
        }
        icon = icons.get(reason, "•")
        
        if reason == "like":
            print(f"{icon} @{author} liked your post · {time_str}")
        elif reason == "repost":
            print(f"{icon} @{author} reposted · {time_str}")
        elif reason == "follow":
            print(f"{icon} @{author} followed you · {time_str}")
        elif reason == "reply":
            print(f"{icon} @{author} replied · {time_str}")
        elif reason == "mention":
            print(f"{icon} @{author} mentioned you · {time_str}")
        elif reason == "quote":
            print(f"{icon} @{author} quoted you · {time_str}")
        else:
            print(f"{icon} {reason} from @{author} · {time_str}")

def cmd_accounts(args):
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    active = cfg.get("active")

    if not profiles:
        print("No profiles configured. Use: bsky login --name <profile> --handle <handle> --password <app-password>")
        return

    for name, p in profiles.items():
        star = "*" if name == active else " "
        handle = p.get("handle") or "(missing handle)"
        did = p.get("did") or "(no did)"
        print(f"{star} {name}: {handle}  {did}")


def cmd_use(args):
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    if args.name not in profiles:
        print(f"Unknown profile: {args.name}", file=sys.stderr)
        sys.exit(1)
    cfg["active"] = args.name
    save_config(cfg)
    print(f"Active profile set to '{args.name}'")


def cmd_logout(args):
    cfg = load_config()
    profiles = cfg.get("profiles") or {}
    if args.name not in profiles:
        print(f"Unknown profile: {args.name}", file=sys.stderr)
        sys.exit(1)

    del profiles[args.name]
    cfg["profiles"] = profiles

    if cfg.get("active") == args.name:
        cfg["active"] = next(iter(profiles.keys()), None)

    save_config(cfg)
    print(f"Removed profile '{args.name}'")


def main():
    parser = argparse.ArgumentParser(description="Bluesky CLI")
    parser.add_argument(
        "--profile",
        help="Profile name to use for this command (overrides active/BSKY_PROFILE)",
        default=None,
    )
    parser.add_argument(
        "--no-throttle",
        action="store_true",
        help="Disable client-side request throttling (not recommended when running commands in parallel)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # login
    login_p = subparsers.add_parser("login", help="Login to Bluesky (creates/updates a named profile)")
    login_p.add_argument("--name", help="Profile name to save under (e.g. work, personal)")
    login_p.add_argument("--handle", required=True, help="Your handle (e.g. user.bsky.social)")
    login_p.add_argument("--password", required=True, help="App password (not your main password)")
    login_p.add_argument("--set-active", action="store_true", help="Make this profile the active default")

    # accounts
    subparsers.add_parser("accounts", help="List configured profiles")

    # use
    use_p = subparsers.add_parser("use", help="Set the active profile")
    use_p.add_argument("name", help="Profile name")

    # logout
    logout_p = subparsers.add_parser("logout", help="Remove a saved profile")
    logout_p.add_argument("name", help="Profile name")

    # whoami
    subparsers.add_parser("whoami", help="Show current user")
    
    # timeline
    tl_p = subparsers.add_parser("timeline", aliases=["tl", "home"], help="Show home timeline")
    tl_p.add_argument("-n", "--count", type=int, default=10, help="Number of posts")
    
    # post
    post_p = subparsers.add_parser("post", aliases=["p"], help="Create a post")
    post_p.add_argument("text", help="Post text")

    # follow / unfollow
    follow_p = subparsers.add_parser("follow", aliases=["f"], help="Follow a user")
    follow_p.add_argument("actor", nargs="?", help="Handle (e.g. @user.bsky.social) or DID")
    follow_p.add_argument("--list", help="Path to a newline-delimited list of handles/DIDs")
    # Defaults are intentionally conservative for write operations.
    # See: https://docs.bsky.app/docs/advanced-guides/rate-limits
    follow_p.add_argument("--min-delay", type=float, default=2.2, help="Min delay between requests (seconds)")
    follow_p.add_argument("--max-delay", type=float, default=3.6, help="Max delay between requests (seconds)")
    follow_p.add_argument("--buffer", type=float, default=0.1, help="Extra delay buffer (e.g. 0.1 = +10%%)")
    follow_p.add_argument("--max", type=int, default=None, help="Max number of entries from the list")
    follow_p.add_argument("--out-followed", dest="out_followed", help="Write followed actors (one per line)")
    follow_p.add_argument("--out-skipped", dest="out_skipped", help="Write skipped actors (already following)")
    follow_p.add_argument("--out-failed", dest="out_failed", help="Write failed actors")
    follow_p.add_argument("--out-remaining", dest="out_remaining", help="Write remaining (not-yet-followed) actors")
    follow_p.add_argument(
        "--inplace",
        action="store_true",
        help="Rewrite --list as a queue (remove processed items) so you can resume after abort",
    )
    follow_p.add_argument("--rewrite-input", action="store_true", help="Rewrite --list file to contain only failures (for rerun)")
    follow_p.add_argument("--dry-run", action="store_true", help="Print actions without calling the API")

    unfollow_p = subparsers.add_parser("unfollow", aliases=["uf"], help="Unfollow a user")
    unfollow_p.add_argument("actor", nargs="?", help="Handle (e.g. @user.bsky.social) or DID")
    unfollow_p.add_argument("--list", help="Path to a newline-delimited list of handles/DIDs")
    # Defaults are intentionally conservative for write operations.
    # See: https://docs.bsky.app/docs/advanced-guides/rate-limits
    unfollow_p.add_argument("--min-delay", type=float, default=2.2, help="Min delay between requests (seconds)")
    unfollow_p.add_argument("--max-delay", type=float, default=3.6, help="Max delay between requests (seconds)")
    unfollow_p.add_argument("--buffer", type=float, default=0.1, help="Extra delay buffer (e.g. 0.1 = +10%%)")
    unfollow_p.add_argument("--max", type=int, default=None, help="Max number of entries from the list")
    unfollow_p.add_argument("--out-unfollowed", dest="out_unfollowed", help="Write unfollowed actors (one per line)")
    unfollow_p.add_argument("--out-skipped", dest="out_skipped", help="Write skipped actors (not following)")
    unfollow_p.add_argument("--out-failed", dest="out_failed", help="Write failed actors")
    unfollow_p.add_argument("--out-remaining", dest="out_remaining", help="Write remaining (not-yet-unfollowed) actors")
    unfollow_p.add_argument(
        "--inplace",
        action="store_true",
        help="Rewrite --list as a queue (remove processed items) so you can resume after abort",
    )
    unfollow_p.add_argument("--rewrite-input", action="store_true", help="Rewrite --list file to contain only failures (for rerun)")
    unfollow_p.add_argument("--dry-run", action="store_true", help="Print actions without calling the API")

    # like / unlike
    like_p = subparsers.add_parser("like", aliases=["l"], help="Like a post by URL")
    like_p.add_argument("post", help="bsky.app post URL")

    unlike_p = subparsers.add_parser("unlike", aliases=["ul"], help="Remove your like from a post by URL")
    unlike_p.add_argument("post", help="bsky.app post URL")

    # repost / unrepost
    repost_p = subparsers.add_parser("repost", aliases=["rp"], help="Repost (boost) a post by URL")
    repost_p.add_argument("post", help="bsky.app post URL")

    unrepost_p = subparsers.add_parser("unrepost", aliases=["urp"], help="Remove your repost from a post by URL")
    unrepost_p.add_argument("post", help="bsky.app post URL")

    # quote / cite
    quote_p = subparsers.add_parser("quote", aliases=["cite", "q"], help="Quote/cite a post with your own text")
    quote_p.add_argument("post", help="bsky.app post URL")
    quote_p.add_argument("text", help="Your quote text")

    # delete
    del_p = subparsers.add_parser("delete", aliases=["del", "rm"], help="Delete a post")
    del_p.add_argument("post_id", help="Post ID or URL")
    
    # profile
    profile_p = subparsers.add_parser("profile", help="Show profile")
    profile_p.add_argument("handle", nargs="?", help="Handle to look up (default: self)")
    
    # search
    search_p = subparsers.add_parser("search", aliases=["s"], help="Search posts")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument("-n", "--count", type=int, default=10, help="Number of results")
    
    # notifications
    notif_p = subparsers.add_parser("notifications", aliases=["notif", "n"], help="Show notifications")
    notif_p.add_argument("-n", "--count", type=int, default=20, help="Number of notifications")
    
    args = parser.parse_args()

    global THROTTLE_ENABLED
    THROTTLE_ENABLED = not bool(getattr(args, "no_throttle", False))

    commands = {
        "login": cmd_login,
        "accounts": cmd_accounts,
        "use": cmd_use,
        "logout": cmd_logout,
        "whoami": cmd_whoami,
        "timeline": cmd_timeline,
        "tl": cmd_timeline,
        "home": cmd_timeline,
        "post": cmd_post,
        "p": cmd_post,
        "follow": cmd_follow,
        "f": cmd_follow,
        "unfollow": cmd_unfollow,
        "uf": cmd_unfollow,
        "like": cmd_like,
        "l": cmd_like,
        "unlike": cmd_unlike,
        "ul": cmd_unlike,
        "repost": cmd_repost,
        "rp": cmd_repost,
        "unrepost": cmd_unrepost,
        "urp": cmd_unrepost,
        "quote": cmd_quote,
        "cite": cmd_quote,
        "q": cmd_quote,
        "delete": cmd_delete,
        "del": cmd_delete,
        "rm": cmd_delete,
        "profile": cmd_profile,
        "search": cmd_search,
        "s": cmd_search,
        "notifications": cmd_notifications,
        "notif": cmd_notifications,
        "n": cmd_notifications,
    }
    
    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
