# Project rules

- Read docs/design.md before architectural changes.
- Preserve the required workflow: fresh screenshot → ScreenSeekeR grounding → double-click the
  desktop alias → type and save through the TextEdit UI. No Spotlight, `open -a`, accessibility
  lookups, or stored icon templates for finding/launching the app.
- Keep grounding/ independent of the application, the desktop executor, and the post workflow.
- Keep model-specific code inside grounding/backends.py.
- Centralize coordinate transformations in geometry.py.
- No empty abstractions, speculative frameworks, or duplicate pipelines.
- Never present mocked/replayed tests as real desktop success.
- Run `uv run --locked pytest` before committing; report failures honestly.
- Inspect `git status` and staged changes before each commit.
- Never commit credentials (.env), private screenshots, model weights, or caches.
- Do not push to a remote without explicit approval.
