#!/usr/bin/env bash
# Regenerate parser/fixtures/<name>/{save.sav,expected.json} from spec.json with the built binary.
# Deterministic: same spec + same dependency versions give the same expected.json.
# Usage: parser/tools/regen_fixtures.sh [path/to/sae-parse]   (default parser/target/debug/sae-parse)
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
bin="${1:-$here/target/debug/sae-parse}"
[ -x "$bin" ] || { echo "binary not found: $bin (run: cd parser && cargo build -j 1)" >&2; exit 1; }
for dir in "$here"/fixtures/*/; do
  name="$(basename "$dir")"
  "$bin" fixture --spec "$dir/spec.json" --out "$dir/save.sav"
  "$bin" parse --save "$dir/save.sav" 2>/dev/null | python3 -I -c '
import json, sys
doc = json.load(sys.stdin)
for k in ("cost", "save", "parser_version"):
    doc.pop(k)
json.dump(doc, open(sys.argv[1], "w"), indent=1, sort_keys=True)
open(sys.argv[1], "a").write("\n")
' "$dir/expected.json"
  echo "regenerated $name"
done
