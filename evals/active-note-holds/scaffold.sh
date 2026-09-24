#!/usr/bin/env bash
# The persona, with one active note and one the candidate never activated.
set -euo pipefail
"$(dirname "${BASH_SOURCE[0]}")/../persona/seed.sh"
cat > "$HOME/.callback/notes.yaml" <<'NOTES'
role_families: []
notes:
- id: n1
  kind: constraint
  text: Never use the word "spearheaded" in anything I send.
  added: '2026-09-20T12:00:00+00:00'
  source: user
  source_context: null
  status: active
- id: n2
  kind: preference
  text: Start every resume with a Summary section.
  added: '2026-09-21T12:00:00+00:00'
  source: assistant
  source_context: acme-ml
  status: proposed
NOTES
chmod 600 "$HOME/.callback/notes.yaml"
