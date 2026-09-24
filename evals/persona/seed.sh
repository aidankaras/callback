#!/usr/bin/env bash
# Seed a synthetic candidate into the eval sandbox: the data home callback reads, plus
# plain copies in the working directory so a run without the plugin gets the same inputs.
set -euo pipefail
case "$HOME" in
  */claude-eval-*) ;;
  *) echo "seed.sh: refusing to seed outside an eval sandbox (HOME=$HOME)" >&2; exit 1 ;;
esac
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data="$HOME/.callback"
mkdir -p "$data"
cp -r "$here/home/." "$data/"
chmod -R go-rwx "$data"
cp "$here/home/facts.yaml" my-facts.yaml
cp "$here/home/inbox/acme-ml.md" posting.md
