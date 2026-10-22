# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Contact the maintainer
privately via GitHub (@LiuShiW) with a description and reproduction steps.

## Important security notes

- API keys are stored locally in `config/models.yaml`. Never commit this file.
- The agent can execute commands in the local `workspace/` through a whitelist.
  The current implementation is **not a sandbox**. Do not run untrusted code or
  prompts on a machine with sensitive data.
- Distributed builds must not contain a real API key. Use the first-run setup
  dialog or ship `models.example.yaml` only.
