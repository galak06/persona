# Contributing

Thanks for taking a look. Issues and pull requests are both welcome.

## Getting set up

```bash
git clone https://github.com/galak06/persona.git
cd persona
pip install -r app/requirements.txt -r app/requirements-dev.txt
python cli.py demo          # should write files to ./out/
```

Working on the studio needs nothing else. Working on the platform (API,
frontend, publishing, engagement) needs the setup in [`app/SETUP.md`](app/SETUP.md).

## Before you open a PR

CI runs lint, format, types, and tests. Run them locally from `app/`:

```bash
cd app
python -m ruff check studio/ tests/test_studio.py ../cli.py
python -m ruff format --check studio/ tests/test_studio.py ../cli.py
python -m mypy studio/ ../cli.py
python -m pytest tests/test_studio.py -q
```

**CI gates an explicit file list, not the whole tree.** If you add a
module or a test, add it to the lists in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) — otherwise it is
never checked, and a green build will not mean what you think it means.

## House rules

A few conventions this codebase holds to:

- **Files stay under ~300 lines.** Split before you exceed it.
- **Type hints on function signatures.** mypy runs in strict mode on the
  gated list.
- **No brand-specific values in engine code.** Anything about a specific
  brand comes from `config.json`. If you need a new field, add it to
  `app/config.example.json` too.
- **Never put a credential in a URL.** Query-string keys end up in httpx's
  request log, and from there in whatever a user pastes into a bug report.
  Use a header. There are tests asserting this.
- **Don't invent facts about someone's brand.** When a config leaves a
  field blank, prompts must stay abstract rather than guess — a brand that
  didn't say what its mascot is must not have a species assigned to it.

## Reporting a security issue

Please don't open a public issue for a vulnerability. See
[SECURITY.md](SECURITY.md).

## Scope

Persona is a self-hosted tool for people publishing their own brand's
content. Pull requests aimed at evading platform rate limits or detection
won't be merged.
