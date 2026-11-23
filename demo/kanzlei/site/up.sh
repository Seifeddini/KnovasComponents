#!/usr/bin/env bash
# Start dc and sql and wait until both are healthy. Builds the image on first use; the first dc start provisions
# the domain from directory.json (about 20 s), later starts reuse the volumes.
. "$(dirname "$0")/_lib.sh"
load_env
compose up -d --wait --wait-timeout 600 dc sql
compose ps
