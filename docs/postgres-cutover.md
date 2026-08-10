# PostgreSQL staged cutover

The local profile keeps SQLite as the zero-dependency default. After the
comparison gates pass, use the explicit `postgres` deployment profile so the
backend defaults are selected consistently instead of being assembled by hand:

```powershell
$env:CHUNK_STUDIO_DEPLOYMENT_PROFILE = 'postgres'
$env:CHUNK_STUDIO_DATABASE_BACKEND = 'postgres'
$env:CHUNK_STUDIO_CONTENT_READ_BACKEND = 'postgres'
$env:CHUNK_STUDIO_VECTOR_BACKEND = 'pgvector'
$env:CHUNK_STUDIO_OBJECT_STORAGE_BACKEND = 'minio'
$env:CHUNK_STUDIO_OBJECT_STORAGE_BUCKET = 'chunk-studio'
$env:CHUNK_STUDIO_OBJECT_STORAGE_ENDPOINT_URL = 'http://127.0.0.1:59000'
$env:CHUNK_STUDIO_REDIS_URL = 'redis://:chunkstudio_dev_only@127.0.0.1:56379/0'
$env:CHUNK_STUDIO_RUN_IN_PROCESS_WORKER = '0'
```

The explicit backend lines are shown for readability and may be omitted when
`CHUNK_STUDIO_DEPLOYMENT_PROFILE=postgres` is used. The profile still requires
the PostgreSQL DSN, Redis URL, MinIO/S3 bucket and credentials below.

When PostgreSQL or trusted-proxy authentication is enabled, distributed
runtime guardrails are enabled by default: Redis, shared S3/MinIO storage, and
an external Worker are required. A local single-process experiment may opt out
explicitly with `CHUNK_STUDIO_REQUIRE_DISTRIBUTED_RUNTIME=0`; that override is
not suitable for a multi-instance deployment.

Derived Markdown, layout ZIPs, and crop images are migrated separately. The
command is dry-run by default and keeps local files as rollback copies:

```powershell
$env:PYTHONPATH = 'backend'
uv run python scripts/migrate_derived_artifacts_to_object_storage.py
uv run python scripts/migrate_derived_artifacts_to_object_storage.py --apply
```

Shared LLM/OCR/MCP settings use a separate PostgreSQL key/value boundary:

```powershell
uv run python scripts/migrate_settings_to_postgres.py `
  --sqlite-path backend/data/chunkstudio.db `
  --dsn $env:CHUNK_STUDIO_DATABASE_URL
uv run python scripts/migrate_settings_to_postgres.py `
  --sqlite-path backend/data/chunkstudio.db `
  --dsn $env:CHUNK_STUDIO_DATABASE_URL `
  --apply
```

Before changing the default database/vector backend, run the read-only cutover
gate. It compares workspace-scoped identifiers and key fields, checks object
metadata coverage, and reports PostgreSQL queue state:

```powershell
uv run python scripts/verify_postgres_cutover.py `
  --sqlite-path backend/data/chunkstudio.db `
  --dsn $env:CHUNK_STUDIO_DATABASE_URL `
  --workspace-id local-workspace
```

The gate must pass together with the existing pgvector top-K comparison. A
non-empty `failed` queue count is reported for operations review; it is not
silently treated as a successful migration.

The gate also requires every workspace-approved chunk to have a matching
pgvector row for the selected model and dimension. A count match alone is not
enough for switching: the existing top-K comparison must still pass.

## Worker boundary

When `CHUNK_STUDIO_DATABASE_BACKEND=postgres`, the PostgreSQL queue,
parse/chunk/OCR mutations, incremental Embedding writes, audit configuration
reads, and assistant-init draft/apply writes use the same workspace-scoped
PostgreSQL boundary (`chunk_vector_index` for vectors). The audit workflow
subprocess receives the request workspace identity explicitly, so it does not
silently fall back to the local default workspace.

The same setting also moves request membership checks and the current-workspace
API to PostgreSQL (`users`, `workspaces`, and `workspace_members`). A trusted
proxy user is accepted only when that exact user/workspace pair is active in
PostgreSQL; the client still cannot submit or override `workspace_id`.

The content CRUD routers and a few legacy SQLite-only retrieval/settings paths
remain outside this boundary. Keep SQLite as the default until those paths are
migrated and the vector coverage plus top-K comparison gates pass.
