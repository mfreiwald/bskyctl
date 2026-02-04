#!/usr/bin/env python3
"""bskyctl - pragmatic Bluesky (AT Protocol) CLI.

This project started life as an OpenClaw skill script and is now packaged as a
standalone CLI.
"""

from __future__ import annotations

import argparse

from .commands.auth import cmd_accounts, cmd_login, cmd_logout, cmd_use, cmd_whoami
from .commands.discover import cmd_notifications, cmd_search
from .commands.feed import cmd_timeline
from .commands.graph import cmd_graph
from .commands.interactions import cmd_like, cmd_repost, cmd_unlike, cmd_unrepost
from .commands.posts import cmd_delete, cmd_post, cmd_profile, cmd_quote
from .commands.social import cmd_follow, cmd_unfollow
from .ratelimit import set_throttle_enabled


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="bskyctl - Bluesky CLI")
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
    follow_p.add_argument(
        "--out-skipped",
        dest="out_skipped",
        help="Write skipped actors (already following)",
    )
    follow_p.add_argument("--out-failed", dest="out_failed", help="Write failed actors")
    follow_p.add_argument(
        "--out-remaining",
        dest="out_remaining",
        help="Write remaining (not-yet-followed) actors",
    )
    follow_p.add_argument(
        "--inplace",
        action="store_true",
        help="Rewrite --list as a queue (remove processed items) so you can resume after abort",
    )
    follow_p.add_argument(
        "--rewrite-input",
        action="store_true",
        help="Rewrite --list file to contain only failures (for rerun)",
    )
    follow_p.add_argument("--dry-run", action="store_true", help="Print actions without calling the API")

    unfollow_p = subparsers.add_parser("unfollow", aliases=["uf"], help="Unfollow a user")
    unfollow_p.add_argument("actor", nargs="?", help="Handle (e.g. @user.bsky.social) or DID")
    unfollow_p.add_argument("--list", help="Path to a newline-delimited list of handles/DIDs")
    # Defaults are intentionally conservative for write operations.
    # See: https://docs.bsky.app/docs/advanced-guides/rate-limits
    unfollow_p.add_argument(
        "--min-delay",
        type=float,
        default=2.2,
        help="Min delay between requests (seconds)",
    )
    unfollow_p.add_argument(
        "--max-delay",
        type=float,
        default=3.6,
        help="Max delay between requests (seconds)",
    )
    unfollow_p.add_argument("--buffer", type=float, default=0.1, help="Extra delay buffer (e.g. 0.1 = +10%%)")
    unfollow_p.add_argument("--max", type=int, default=None, help="Max number of entries from the list")
    unfollow_p.add_argument(
        "--out-unfollowed",
        dest="out_unfollowed",
        help="Write unfollowed actors (one per line)",
    )
    unfollow_p.add_argument(
        "--out-skipped",
        dest="out_skipped",
        help="Write skipped actors (not following)",
    )
    unfollow_p.add_argument("--out-failed", dest="out_failed", help="Write failed actors")
    unfollow_p.add_argument(
        "--out-remaining",
        dest="out_remaining",
        help="Write remaining (not-yet-unfollowed) actors",
    )
    unfollow_p.add_argument(
        "--inplace",
        action="store_true",
        help="Rewrite --list as a queue (remove processed items) so you can resume after abort",
    )
    unfollow_p.add_argument(
        "--rewrite-input",
        action="store_true",
        help="Rewrite --list file to contain only failures (for rerun)",
    )
    unfollow_p.add_argument("--dry-run", action="store_true", help="Print actions without calling the API")

    # like / unlike
    like_p = subparsers.add_parser("like", aliases=["l"], help="Like a post by URL")
    like_p.add_argument("post", help="bsky.app post URL")

    unlike_p = subparsers.add_parser("unlike", aliases=["ul"], help="Remove your like from a post by URL")
    unlike_p.add_argument("post", help="bsky.app post URL")

    # repost / unrepost
    repost_p = subparsers.add_parser("repost", aliases=["rp"], help="Repost (boost) a post by URL")
    repost_p.add_argument("post", help="bsky.app post URL")

    unrepost_p = subparsers.add_parser(
        "unrepost",
        aliases=["urp"],
        help="Remove your repost from a post by URL",
    )
    unrepost_p.add_argument("post", help="bsky.app post URL")

    # quote / cite
    quote_p = subparsers.add_parser(
        "quote",
        aliases=["cite", "q"],
        help="Quote/cite a post with your own text",
    )
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

    # graph
    graph_p = subparsers.add_parser("graph", help="Graph ops (followers/follows)")
    graph_sp = graph_p.add_subparsers(dest="graph_command")

    graph_export_p = graph_sp.add_parser(
        "export",
        help="Export followers and follows of an actor to a text file",
    )
    graph_export_p.add_argument("actor", help="Handle (e.g. user.bsky.social) or DID")
    graph_export_p.add_argument("--out", required=True, help="Output .txt file path")
    graph_export_p.add_argument(
        "--only",
        choices=["both", "followers", "follows"],
        default="both",
        help="Which lists to export",
    )
    graph_export_p.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Page size for API calls (max 100)",
    )
    graph_export_p.add_argument(
        "--format",
        choices=["handle", "did", "handle+did"],
        default="handle",
        help="Line format",
    )
    graph_export_p.add_argument(
        "--plain",
        action="store_true",
        help="Write a plain newline-delimited list (no headers/sections). Best for piping into --list.",
    )
    graph_export_p.add_argument(
        "--progress-every",
        type=int,
        default=500,
        help="Print progress every N items (0 disables)",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    set_throttle_enabled(not bool(getattr(args, "no_throttle", False)))

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
        "graph": cmd_graph,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
