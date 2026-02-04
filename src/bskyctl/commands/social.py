from __future__ import annotations

import random
import sys
import time

from ..config import get_client
from ..ratelimit import is_already_exists, is_rate_limited, throttle_req
from ..utils import (
    append_line,
    atomic_write_lines,
    normalize_handle,
    read_actor_lines,
    rewrite_list_file,
    sleep_between,
)


def cmd_follow(args) -> None:
    client = get_client(profile=args.profile)

    try:
        actors: list[str]
        list_path = getattr(args, "list", None)
        if list_path:
            actors = read_actor_lines(list_path)
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
            a = normalize_handle(raw)
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
                rewrite_list_file(list_path, remaining)
            atomic_write_lines(args.out_remaining, remaining) if args.out_remaining else None

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
                    append_line(args.out_followed, actor)
                    remaining.pop(0)
                    processed += 1
                    checkpoint()
                    sleep_between(args.min_delay, args.max_delay, args.buffer)
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
                                throttle_req(1.0)
                                did = client.resolve_handle(actor).did
                                did_cache[actor] = did

                        throttle_req(1.0)
                        client.follow(did)
                        print(f"Followed ({idx}/{len(actors)}): {actor}")
                        ok.append(actor)
                        append_line(args.out_followed, actor)
                        # success => remove from queue
                        remaining.pop(0)
                        processed += 1
                        handled = True
                        break
                    except Exception as e:
                        if is_already_exists(e):
                            print(f"Already following ({idx}/{len(actors)}): {actor}")
                            skipped.append(actor)
                            append_line(args.out_skipped, actor)
                            remaining.pop(0)
                            processed += 1
                            handled = True
                            break

                        if is_rate_limited(e) and attempt < 2:
                            wait_s = random.uniform(20, 40) * (1.0 + float(args.buffer))
                            print(f"Rate limited; backing off {wait_s:.1f}s ...", file=sys.stderr)
                            time.sleep(wait_s)
                            attempt += 1
                            continue

                        msg = str(e)
                        print(f"Follow failed ({idx}/{len(actors)}): {actor} :: {msg}", file=sys.stderr)
                        failed.append(actor)
                        append_line(args.out_failed, actor)
                        # failure => move to end (so we still progress)
                        remaining.pop(0)
                        remaining.append(actor)
                        processed += 1
                        handled = True
                        break

                if handled:
                    checkpoint()
                sleep_between(args.min_delay, args.max_delay, args.buffer)

        except KeyboardInterrupt:
            print("Interrupted. Writing remaining list for resume...", file=sys.stderr)
            checkpoint()
            raise

        if args.rewrite_input and list_path and not args.inplace:
            # Keep only failures by default so a rerun targets what didn't work.
            rewrite_list_file(list_path, failed)
        elif args.rewrite_input and args.inplace:
            print("Note: --rewrite-input ignored when --inplace is set.", file=sys.stderr)

        print(f"Done. followed={len(ok)} skipped={len(skipped)} failed={len(failed)}")
    except Exception as e:
        print(f"Follow failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_unfollow(args) -> None:
    client = get_client(profile=args.profile)

    try:
        actors: list[str]
        list_path = getattr(args, "list", None)
        if list_path:
            actors = read_actor_lines(list_path)
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
            a = normalize_handle(raw)
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
                rewrite_list_file(list_path, remaining)
            atomic_write_lines(args.out_remaining, remaining) if args.out_remaining else None

        try:
            processed = 0
            while remaining:
                actor = remaining[0]
                idx = processed + 1

                if args.dry_run:
                    print(f"DRY RUN unfollow: {actor}")
                    ok.append(actor)
                    append_line(args.out_unfollowed, actor)
                    remaining.pop(0)
                    processed += 1
                    checkpoint()
                    sleep_between(args.min_delay, args.max_delay, args.buffer)
                    continue

                attempt = 0
                handled = False
                while True:
                    try:
                        throttle_req(1.0)
                        profile = client.get_profile(actor)
                        viewer = getattr(profile, "viewer", None)
                        follow_uri = getattr(viewer, "following", None) if viewer else None
                        if not follow_uri:
                            print(f"Not following ({idx}/{len(actors)}): {actor}")
                            skipped.append(actor)
                            append_line(args.out_skipped, actor)
                        else:
                            throttle_req(1.0)
                            client.unfollow(follow_uri)
                            print(f"Unfollowed ({idx}/{len(actors)}): {actor}")
                            ok.append(actor)
                            append_line(args.out_unfollowed, actor)

                        remaining.pop(0)
                        processed += 1
                        handled = True
                        break
                    except Exception as e:
                        if is_rate_limited(e) and attempt < 2:
                            wait_s = random.uniform(20, 40) * (1.0 + float(args.buffer))
                            print(f"Rate limited; backing off {wait_s:.1f}s ...", file=sys.stderr)
                            time.sleep(wait_s)
                            attempt += 1
                            continue
                        msg = str(e)
                        print(f"Unfollow failed ({idx}/{len(actors)}): {actor} :: {msg}", file=sys.stderr)
                        failed.append(actor)
                        append_line(args.out_failed, actor)
                        remaining.pop(0)
                        remaining.append(actor)
                        processed += 1
                        handled = True
                        break

                if handled:
                    checkpoint()
                sleep_between(args.min_delay, args.max_delay, args.buffer)

        except KeyboardInterrupt:
            print("Interrupted. Writing remaining list for resume...", file=sys.stderr)
            checkpoint()
            raise

        if args.rewrite_input and list_path and not args.inplace:
            rewrite_list_file(list_path, failed)
        elif args.rewrite_input and args.inplace:
            print("Note: --rewrite-input ignored when --inplace is set.", file=sys.stderr)

        print(f"Done. unfollowed={len(ok)} skipped={len(skipped)} failed={len(failed)}")
    except Exception as e:
        print(f"Unfollow failed: {e}", file=sys.stderr)
        raise SystemExit(1)
