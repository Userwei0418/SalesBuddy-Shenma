#!/bin/zsh
set -e
cd -- "$(dirname -- "$0")"
exec node scripts/start_shenzhou_preview.mjs
