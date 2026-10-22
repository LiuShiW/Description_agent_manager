# Contributing

Thanks for your interest! This project is a local-first multi-agent software team.

## Development setup

```bash
git clone https://github.com/LiuShiW/Description_agent_manager.git
cd Description_agent_manager
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Run the desktop app:

```bash
python app/desktop_app.py
```

On first launch, a setup dialog asks for your API key (BYOK). You can also copy
`config/models.example.yaml` to `config/models.yaml` and edit it manually.

## Before submitting a PR

- Keep changes focused and small.
- Run a syntax check: `python -m py_compile main.py app/desktop_app.py src/**/*.py`
- If you change the pipeline, run a small end-to-end task and describe the result.
- Do not commit API keys, generated `workspace/` output, or build artifacts.

## Commit style

Use short imperative messages, e.g. `feat: add project switcher`, `fix: hide subprocess windows`.

## Code of conduct

Be respectful and constructive. Harassment or personal attacks are not tolerated.
