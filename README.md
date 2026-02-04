# bskyctl

A pragmatic **Bluesky (AT Protocol)** command-line tool focused on **automation**:

- multi-profile auth stored in `~/.config/bsky/config.json`
- safe **client-side throttling** across parallel processes
- batch operations with resume (`--inplace`)
- proper **facets** for links, hashtags and @mentions

> Note: Bluesky has official rate limits; bskyctl defaults are conservative.

## Install (dev/local)

### Option A: pipx (recommended)

```bash
cd ~/Projects/bskyctl
pipx install -e .

bskyctl --help
```

### Option B: uv tool

```bash
cd ~/Projects/bskyctl
uv tool install -e .
```

## Quickstart

```bash
# login (uses an app password)
bskyctl login --name personal --handle you.bsky.social --password xxxx-xxxx-xxxx-xxxx --set-active

# timeline
bskyctl timeline -n 20

# search
bskyctl search "skydeck" -n 20

# batch follow (resume-safe)
bskyctl follow --list newfollowers.txt --inplace \
  --out-followed followed.txt --out-skipped already.txt --out-failed failed.txt
```

## Rate limiting / throttling

Client-side request throttling is enabled by default.

- Disable (not recommended): `--no-throttle`
- Tune via env vars:
  - `BSKY_REQ_RPS` (default `8`)
  - `BSKY_REQ_BURST` (default `16`)

## License

MIT
