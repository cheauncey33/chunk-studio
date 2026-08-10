# PostgreSQL staged cutover

The current application keeps SQLite as the default content read/write path.
The first production-like slice is opt-in and read-only for knowledge bases,
files, chunks, and parse metadata:

```powershell
$env:CHUNK_STUDIO_CONTENT_READ_BACKEND = 'postgres'
$env:CHUNK_STUDIO_OBJECT_STORAGE_BACKEND = 'minio'
$env:CHUNK_STUDIO_OBJECT_STORAGE_BUCKET = 'chunk-studio'
$env:CHUNK_STUDIO_OBJECT_STORAGE_ENDPOINT_URL = 'http://127.0.0.1:59000'
```

Derived Markdown, layout ZIPs, and crop images are migrated separately. The
command is dry-run by default and keeps local files as rollback copies:

```powershell
$env:PYTHONPATH = 'backend'
uv run python scripts/migrate_derived_artifacts_to_object_storage.py
uv run python scripts/migrate_derived_artifacts_to_object_storage.py --apply
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

The content CRUD routers and a few legacy SQLite-only retrieval/settings paths
remain outside this boundary. Keep SQLite as the default until those paths are
migrated and the vector coverage plus top-K comparison gates pass.
