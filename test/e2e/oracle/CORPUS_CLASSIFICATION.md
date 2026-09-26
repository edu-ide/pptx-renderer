# Corpus Classification

Capability inventory keeps two separate signals:

- **representative** packages are explicitly selected human-authored, issue, or product documents
  that may influence roadmap priority.
- **validation** packages are generated matrices and other fixtures that expand regression coverage
  without inflating roadmap demand.

Pass one or more inventory-alias globs when creating a decision inventory:

```bash
python3 test/e2e/scripts/run_capability_loop.py inventory \
  --corpus test/e2e/testdata/cases \
  --corpus test/e2e/testdata/windows-cases \
  --representative-alias 'corpus-0/representative-deck/source.pptx' \
  --out test/e2e/reports/capability-loop/inventory.json
```

Aliases are local report identifiers derived from the ordered `--corpus` arguments. Reports remain
ignored, so a local profile may name private test cases without publishing them. Each PPTX is still
scanned once and deduplicated by SHA-256. If any alias of a byte-identical package matches, that
package is representative. An explicit glob that matches no accepted or rejected input fails the
inventory command to catch misspellings. With no globs, every package remains representative for
backward compatibility.

Ranking compares impact and currently reproduced issues first, then representative package count,
then total unique package count. Validation fixtures continue to contribute to the total count and
all oracle/regression gates; they simply cannot outrank a capability only because its generated
matrix contains more files.
