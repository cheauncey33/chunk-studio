# Repository Guidelines

## Project Structure & Module Organization

- `backend/app/` contains the FastAPI application. Routers live in `backend/app/routers/`; parsing, schema, database, and PDF helpers remain in their focused modules.
- `frontend/src/` contains the React 19 + TypeScript UI. Keep API types and calls in `api.ts` and shared chunk types in `chunkSchema.ts`.
- `tests/` contains Python regression tests named `test_*.py`.
- `evaluation/` contains versioned audit gold drafts; local reports and rebuilt databases remain untracked fixtures.
- `scripts/` contains repeatable maintenance tools, including chunk rebuilding and quality auditing.
- `backend/data/`, `data/`, `.env`, SQLite files, generated crops, and local source documents are runtime artifacts, not source code.

## Build, Test, and Development Commands

From the repository root (PowerShell):

```powershell
uv sync
docker compose -f docker-compose.engineering.yml up -d
$env:PYTHONPATH='backend'; uv run python scripts/migrate_postgres_schema.py --apply
$env:PYTHONPATH='backend'; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
uv run python scripts/run_worker.py --types ocr,parse,chunk,embed,audit,assistant_init
uv run --with pytest pytest -q
$env:PYTHONPATH='backend'; uv run python scripts/audit_chunk_quality.py
$env:PYTHONPATH='backend'; uv run python scripts/validate_test_set.py
```

The default runtime is PostgreSQL/pgvector. Opt out with `$env:CHUNK_STUDIO_DEPLOYMENT_PROFILE='local'` for the zero-dependency SQLite path. Tests force that local profile automatically.

For the frontend:

```powershell
cd frontend
npm install
npm run dev       # Vite development server
npm run lint      # oxlint
npm run build     # TypeScript check and production build
```

Run `scripts/rebuild_chunks_from_parses.py` only when intentionally replacing all chunk data; it preserves PDFs and completed MinerU parses.

## Coding Style & Naming Conventions

Use four spaces, type hints, `snake_case` functions, and `PascalCase` classes in Python. Follow the existing TypeScript style: two spaces, single quotes, `PascalCase` components, and `camelCase` values. Keep edits scoped; prefer existing helpers and structured parsers over ad hoc string manipulation. Add comments only for non-obvious behavior.

## Testing Guidelines

Add a focused regression test for every parser, metadata, numbering, or chunk-boundary bug. Name tests after observable behavior, for example `test_section_roots_keep_leaf_section_above_target_level`. Run the full Python suite, frontend lint, and frontend build before review. For data-pipeline changes, also run the chunk quality audit and report changed totals or review flags.

## Commit & Pull Request Guidelines

Use short imperative commit subjects consistent with history, such as `Add chunk metadata layering` or `Improve section chunking`. PRs should explain behavior, affected files/data, verification commands, and migration or rebuild requirements. Include screenshots for visible UI changes. Keep generated data and unrelated local changes out of commits.

## Security, Evidence, and Agent Rules

Never commit API keys, `.env`, reports containing sensitive data, or generated databases. Production behavior must be reproducible without a coding agent: encode decisions in versioned code, schemas, prompts, tests, and scripts. Preserve page/bbox evidence for extracted facts. Do not make model-generated metadata an unreviewed hard filter, and do not use unstable chunk UUIDs as long-lived evaluation labels.
