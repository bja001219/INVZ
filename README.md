> Local evaluation only. This app has no authentication and must not be exposed to the public internet or an untrusted LAN. Docker Compose publishes ports on `127.0.0.1` only.

# InvzAssign Prompt-to-Animation MVP

Phase 1 establishes the secret-safe FastAPI, SQLite, CORS, and Mock media foundation. Product features are implemented in later phases described in the tracked design and implementation plan.

## Phase 1 development

```powershell
cd backend
python -m pip install -e ".[dev]"
alembic upgrade head
python -m pytest tests/test_core.py -v
ruff check app tests
mypy app
```
