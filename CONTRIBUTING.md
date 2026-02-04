# Contributing

Thanks for your interest in contributing to **bskyctl**.

## Development setup

```bash
git clone <repo>
cd bskyctl

# recommended
pipx install -e .

# run locally
bskyctl --help
```

## Style / lint

- Python >= 3.11
- Lint: `ruff check .`

## Pull requests

- Keep changes focused and well-described.
- Avoid adding heavy dependencies unless strongly justified.
- Do not add tests/CI that make real network calls to Bluesky/ATProto services.
