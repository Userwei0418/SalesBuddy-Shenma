#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
node -e 'if (Number(process.versions.node.split(".")[0]) < 22) throw new Error("Node.js 22+ is required")'
python3 --version
npm ci --ignore-scripts
npm run verify
