from __future__ import annotations

import re
import sys

from ..atproto_compat import require_atproto
from ..config import get_client
from ..ratelimit import call_with_read_backoff, call_with_write_backoff
from ..utils import normalize_handle
from .postrefs import resolve_post_ref


def cmd_post(args) -> None:
    _Client, client_utils, _models = require_atproto()

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
        response = call_with_write_backoff(lambda: client.send_post(text=text))
    else:
        builder = client_utils.TextBuilder()
        last_end = 0

        for match in matches:
            if match.start() > last_end:
                builder.text(text[last_end : match.start()])

            token = match.group(1)

            if re.fullmatch(url_pattern, token):
                builder.link(token, token)
            elif re.fullmatch(tag_pattern, token):
                # token includes leading '#'
                builder.tag(token, token[1:])
            elif token.startswith("@"):  # mention
                handle = normalize_handle(token)
                try:
                    did = call_with_read_backoff(lambda: client.resolve_handle(handle)).did
                    builder.mention(token, did)
                except Exception:
                    # Fallback to plain text if resolution fails.
                    builder.text(token)
            else:
                builder.text(token)

            last_end = match.end()

        if last_end < len(text):
            builder.text(text[last_end:])

        response = call_with_write_backoff(lambda: client.send_post(builder))

    uri = response.uri
    post_id = uri.split("/")[-1]
    print(f"Posted: https://bsky.app/profile/{client.me.handle}/post/{post_id}")


def cmd_quote(args) -> None:
    _Client, client_utils, models = require_atproto()

    client = get_client(profile=args.profile)
    try:
        uri, cid, public_url = resolve_post_ref(client, args.post)
        embed = models.AppBskyEmbedRecord.Main(record=models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid))

        # Reuse the same rich-text builder behavior as cmd_post (links + hashtags + mentions facets)
        text = args.text
        url_pattern = r"https?://[^\s]+"
        tag_pattern = r"#[A-Za-z0-9_]+"
        mention_pattern = r"@[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)*"
        token_re = re.compile(rf"({url_pattern}|{tag_pattern}|{mention_pattern})")
        matches = list(token_re.finditer(text))

        if not matches:
            response = call_with_write_backoff(lambda: client.send_post(text=text, embed=embed))
        else:
            builder = client_utils.TextBuilder()
            last_end = 0
            for match in matches:
                if match.start() > last_end:
                    builder.text(text[last_end : match.start()])

                token = match.group(1)
                if re.fullmatch(url_pattern, token):
                    builder.link(token, token)
                elif re.fullmatch(tag_pattern, token):
                    builder.tag(token, token[1:])
                elif token.startswith("@"):  # mention
                    handle = normalize_handle(token)
                    try:
                        did = call_with_read_backoff(lambda: client.resolve_handle(handle)).did
                        builder.mention(token, did)
                    except Exception:
                        builder.text(token)
                else:
                    builder.text(token)
                last_end = match.end()
            if last_end < len(text):
                builder.text(text[last_end:])

            response = call_with_write_backoff(lambda: client.send_post(builder, embed=embed))

        post_id = response.uri.split("/")[-1]
        print(f"Quoted: https://bsky.app/profile/{client.me.handle}/post/{post_id}")
        if public_url:
            print(f"  ↳ original: {public_url}")
    except Exception as e:
        print(f"Quote failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_delete(args) -> None:
    client = get_client(profile=args.profile)

    # Extract post ID from URL or use raw ID
    post_id = args.post_id
    if "bsky.app" in post_id:
        post_id = post_id.rstrip("/").split("/")[-1]

    # Construct the URI
    uri = f"at://{client.me.did}/app.bsky.feed.post/{post_id}"

    try:
        call_with_write_backoff(lambda: client.delete_post(uri))
        print(f"Deleted post: {post_id}")
    except Exception as e:
        print(f"Delete failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_profile(args) -> None:
    client = get_client(profile=args.profile)
    handle = args.handle.lstrip("@") if args.handle else client.me.handle

    # Auto-append .bsky.social if no domain specified
    if handle and "." not in handle:
        handle = f"{handle}.bsky.social"

    profile = call_with_read_backoff(lambda: client.get_profile(handle))
    print(f"@{profile.handle}")
    print(f"  Name: {profile.display_name or '(none)'}")
    print(f"  Bio: {profile.description or '(none)'}")
    print(f"  Followers: {profile.followers_count}")
    print(f"  Following: {profile.follows_count}")
    print(f"  Posts: {profile.posts_count}")
    print(f"  DID: {profile.did}")
