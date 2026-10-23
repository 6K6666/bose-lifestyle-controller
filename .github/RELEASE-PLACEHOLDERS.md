# Release placeholders

Every `[[...]]` placeholder in the documentation, the README and the NOTICE must be filled in before a release. The release gate requires this table to be empty of placeholders: each row is removed once its text is filled in, and the lint in `tests/test_docs.py` checks that every placeholder in the documents is listed here.

This file is the one place where placeholders are allowed to appear, because it lists them.

| Placeholder | Where it appears | Who fills it | What to put in |
|---|---|---|---|

## Settled packaging values

- `hacs.json` and `requirements-ha-test.txt` both use Home Assistant `2026.10.0`, the current stable release as of 2026-10-13.
- Every GitHub Action in `.github/workflows/` uses a full commit SHA. The HACS action pin is to the current upstream action commit; its action metadata still points to the mutable `ghcr.io/hacs/action:main` image.
- Keep both brand copies: HACS checks the repository-level `brand/` directory, while Home Assistant custom integrations load `custom_components/bose_lifestyle/brand/`.

## Release-time text and checks

| Item | Where | Who | What to do |
|---|---|---|---|
| `GitHub repository description: Home Assistant control for Bose Lifestyle 50 / M1 systems. Unofficial, not affiliated with Bose.` | GitHub repository settings (description) | Maintainer | Set this when the public repository is created. |
| `HACS validation action` | `.github/workflows/validate.yml` | Maintainer | Confirm its brands check passes on the public candidate. Keep root `brand/` for HACS and the integration `brand/` for Home Assistant. |
