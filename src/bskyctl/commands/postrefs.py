from __future__ import annotations

import re

from ..ratelimit import call_with_read_backoff


def resolve_post_ref(client, value: str) -> tuple[str, str, str | None]:
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
        did = call_with_read_backoff(lambda: client.resolve_handle(handle)).did
        uri = f"at://{did}/app.bsky.feed.post/{rkey}"
        posts = call_with_read_backoff(lambda: client.get_posts([uri])).posts
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
        posts = call_with_read_backoff(lambda: client.get_posts([uri])).posts
        if not posts:
            raise RuntimeError("Could not resolve post")
        post = posts[0]
        return post.uri, post.cid, None

    raise RuntimeError("Unsupported post reference (use a bsky.app post URL)")


def get_viewer_refs(client, uri: str) -> tuple[str | None, str | None]:
    """Return (like_uri, repost_uri) for the authenticated viewer, if present."""

    posts = call_with_read_backoff(lambda: client.get_posts([uri])).posts
    if not posts:
        return None, None
    post = posts[0]
    viewer = getattr(post, "viewer", None)
    if not viewer:
        return None, None
    like_uri = getattr(viewer, "like", None)
    repost_uri = getattr(viewer, "repost", None)
    return like_uri, repost_uri
