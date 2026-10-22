#!/usr/bin/env bash
# Serve h3lab over HTTP so the viewer can fetch the JSON blocks.
# Browsers block fetch() on file://, so opening viewer/index.html directly
# will not work.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8000}"
echo "viewers http://localhost:${PORT}/viewer/"
echo "regional http://localhost:${PORT}/viewer/regional.html"
echo "pilot    http://localhost:${PORT}/viewer/pilot.html"
echo "probe    http://localhost:${PORT}/viewer/probe.html   <- run this first if loading is slow"
echo "(ctrl-c to stop)"
exec python3 -m http.server "$PORT" --bind 127.0.0.1