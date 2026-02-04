from __future__ import annotations

from ..config import get_client
from ..ratelimit import call_with_read_backoff


def cmd_search(args) -> None:
    client = get_client(profile=args.profile)
    response = call_with_read_backoff(
        lambda: client.app.bsky.feed.search_posts({"q": args.query, "limit": args.count})
    )

    if not response.posts:
        print("No results found.")
        return

    for post in response.posts:
        author = post.author.handle
        text = post.record.text if hasattr(post.record, "text") else ""
        likes = post.like_count or 0

        print(f"@{author}: {text[:150]}")
        print(f"  ❤️ {likes}  🔗 https://bsky.app/profile/{author}/post/{post.uri.split('/')[-1]}")
        print()


def cmd_notifications(args) -> None:
    client = get_client(profile=args.profile)
    response = call_with_read_backoff(
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
            "quote": "💭",
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
