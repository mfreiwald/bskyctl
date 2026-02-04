from __future__ import annotations

from datetime import datetime

from ..config import get_client
from ..ratelimit import call_with_read_backoff


def cmd_timeline(args) -> None:
    client = get_client(profile=args.profile)
    response = call_with_read_backoff(lambda: client.get_timeline(limit=args.count))

    for item in response.feed:
        post = item.post
        author = post.author.handle
        text = post.record.text if hasattr(post.record, "text") else ""
        created = post.record.created_at if hasattr(post.record, "created_at") else ""
        likes = post.like_count or 0
        reposts = post.repost_count or 0
        replies = post.reply_count or 0

        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            time_str = dt.strftime("%b %d %H:%M")
        except Exception:
            time_str = created[:16] if created else ""

        print(f"@{author} · {time_str}")
        print(f"  {text[:200]}")
        print(f"  ❤️ {likes}  🔁 {reposts}  💬 {replies}")
        print(f"  🔗 https://bsky.app/profile/{author}/post/{post.uri.split('/')[-1]}")
        print()
