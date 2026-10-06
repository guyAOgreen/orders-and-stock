#!/bin/bash
# Runs once, when the data volume is first created. The development database
# ($POSTGRES_DB) is created by the image; this adds the test database next to it.
set -euo pipefail

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "CREATE DATABASE ${POSTGRES_DB}_test OWNER ${POSTGRES_USER};"
