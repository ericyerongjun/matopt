#!/bin/sh
# The vendored web-artifact skill uses Vite/Parcel native dependencies.
# Limit install-script permission to that toolchain; keep upstream skills verbatim.
case "${1-}" in
  add|install|i)
    artifact_pnpm_action=$1
    shift
    exec /usr/local/lib/node_modules/pnpm/pnpm "$artifact_pnpm_action" \
      --allow-build esbuild --allow-build @parcel/watcher --allow-build @swc/core \
      --allow-build lmdb --allow-build msgpackr-extract "$@"
    ;;
  *) exec /usr/local/lib/node_modules/pnpm/pnpm "$@" ;;
esac
