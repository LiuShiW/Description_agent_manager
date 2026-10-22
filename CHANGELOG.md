# Changelog

## [0.2.0] - 2026-10-23

### Added
- Native PySide6 desktop workbench with group chat and per-role conversations.
- Six-role pipeline: product manager, architect, backend, frontend, reviewer, QA.
- Human approval gates after PRD questions and architecture design.
- Automatic project type detection: backend-only / frontend-only / full-stack.
- Parallel frontend/backend development via `asyncio.gather`.
- Project memory (`memory.json`) with per-role context clipping.
- Token usage tracking per project, run, and role (`usage.json`).
- Multi-project workspaces, session persistence, stop control, settings dialog.
- Markdown and code-block rendering in the desktop UI.
- Test-artifact cleanup after QA.
- PyInstaller packaging with automatic backup/restore of user data.

### Changed
- Removed manual collaboration mode; sequential SOP with automatic parallelism.
- Removed monetary cost estimation and budget limits (token-only usage).
- README rewritten in a personal developer tone, without competitor comparisons.

## [0.1.0] - 2026-10-22
- Initial public preview.
