#!/bin/bash
# Startup - no schema changes, no migrations, no seed. DB managed externally.
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
