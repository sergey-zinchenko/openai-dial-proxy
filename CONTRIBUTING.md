# Contributing

This project is Apache-2.0. By submitting a change you agree that your contribution is licensed under the same terms.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
ruff check .
pytest -q
```

## What to change

- Keep the three public paths exact. Do not route a `/v1` prefix.
- Do not log API keys or bearer tokens.
- If you add a runtime dependency, update `NOTICE` and keep `scripts/check_licenses.py` green.
- Deployment examples stay on `dial/dial-extension` chart 3.1.1 (Ingress and HTTPRoute).

## Pull requests

Open a pull request against `main` with a short description of the behavior change and how you tested it.
