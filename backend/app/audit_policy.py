"""Production policy for the report-audit evidence pipeline."""

PRODUCTION_EVIDENCE_COMPRESSION_MODE = "active"
PRODUCTION_RECOVERY_MODE = "active"

# Recovery is an exception path for explicit evidence gaps, not the default
# reasoning path. Keep its search surface and token budget deliberately small.
RECOVERY_MAX_TURNS = 3
RECOVERY_MAX_TOOL_CALLS = 4
RECOVERY_MAX_SEARCH_CALLS = 2
RECOVERY_TIMEOUT_SECONDS = 90
