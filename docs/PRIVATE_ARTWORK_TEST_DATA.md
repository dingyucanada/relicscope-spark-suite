# Private artwork test-data intake

This workflow accepts an operator-supplied ZIP as **private, unverified test
material**. It does not add the material to the reference library, does not infer
authenticity, and does not make a folder name or filename into a scientific fact.

The default command is a read-only audit. Import requires an explicit batch ID and
writes only beneath the repository-ignored `runtime/private-artworks/` tree.

## 1. Audit first

Use the V2 Python environment after the normal approved installation step:

```bash
make private-artwork-audit \
  PRIVATE_ARTWORK_ARCHIVE=/absolute/path/to/private-artworks.zip
```

Or invoke the script directly:

```bash
.venv-v2/bin/python scripts/import-private-artwork-archive.py \
  /absolute/path/to/private-artworks.zip
```

Audit streams and validates allowed entries but creates no directory, manifest,
cache, or extracted file. The JSON result is printed to standard output. A zero
exit code and `status: VALIDATED` mean that the archive passed the intake policy;
they do not mean that the objects, labels, dates, authorship, provenance, or
authenticity were verified.

## 2. Import an approved batch

After reviewing the audit output, assign a non-sensitive batch ID:

```bash
make private-artwork-import \
  PRIVATE_ARTWORK_ARCHIVE=/absolute/path/to/private-artworks.zip \
  PRIVATE_ARTWORK_BATCH=customer-test-001
```

Equivalent direct invocation:

```bash
.venv-v2/bin/python scripts/import-private-artwork-archive.py \
  /absolute/path/to/private-artworks.zip \
  --import-batch customer-test-001
```

The destination is created atomically and an existing batch is never replaced:

```text
runtime/private-artworks/customer-test-001/
├── manifest.json
├── review.csv
└── objects/
    └── sha256/
        └── ab/
            └── ab…<full-sha256>.png
```

Original ZIP paths are metadata only. Blob paths are derived exclusively from the
validated SHA-256 and canonical media extension. `runtime/` and `*.zip` are already
ignored by Git; keep the source archive outside the repository as an additional
privacy boundary.

## 3. Enforced intake policy

The importer rejects the complete batch when it finds any of the following:

- an absolute, UNC, Windows-drive, empty-component, or `..` traversal path;
- a symbolic link, executable bit, special/device file, or encrypted entry;
- a nested archive, executable signature, unsupported compression method, or
  unsupported file type;
- an extension/content magic mismatch;
- duplicate paths after Unicode normalization and case folding;
- an entry, archive, total expanded size, compression ratio, entry count, or image
  pixel count above the configured limit;
- an image that Pillow cannot fully decode, whose format disagrees with its
  extension, or that contains multiple frames.

Only PNG, JPEG, and PDF are accepted. `__MACOSX/`, AppleDouble `._*`, and
`.DS_Store` entries are ignored after their paths and entry types pass safety
checks. PDF files receive magic validation only and are always marked
`requires_manual_document_review: true`; no PDF text, attachment, script, or claim
is trusted automatically.

The defaults are intentionally bounded:

| Limit | Default |
|---|---:|
| Compressed archive | 2 GiB |
| ZIP entries | 2,000 |
| One entry | 128 MiB |
| Total expanded bytes | 4 GiB |
| Per-entry expansion ratio | 100× |
| One image | 60 megapixels |

All limits have explicit command-line flags. Raising a limit is an operator risk
decision and should be done only for a known batch on a suitably isolated Spark.

## 4. Filename handling

Some macOS ZIP tools place UTF-8 filename bytes in the archive without setting the
ZIP EFS/UTF-8 flag. The importer safely reconstructs those bytes from the ZIP
decoder's CP437 representation, validates any Info-ZIP Unicode Path extra field,
and normalizes the result to Unicode NFC.

Whitespace and terminal dots are removed from normalized path components, so a
synthetic directory such as `artifact-group-001 ` becomes the candidate label
`artifact-group-001`. The unmodified, decoded name remains in `original_path`. A collision created by normalization
rejects the archive instead of silently overwriting a record.

## 5. Review semantics

Every asset is deliberately conservative:

- `candidate_group` is derived only from the first archive directory;
- its provenance is `archive_directory_name`, `UNVERIFIED`, confidence `LOW`;
- `view_role` is always `unclassified` with confidence `NONE`;
- a parenthesized filename number may be retained as a low-confidence
  `sequence_hint`, never as a camera angle;
- provenance is `OPERATOR_SUPPLIED_PRIVATE_ARCHIVE`, `UNVERIFIED`, confidence
  `LOW`;
- `scientific_use_status` blocks identity or authenticity claims until the data is
  separately reviewed and admitted through a governed reference-data process.

Use `review.csv` to assign the physical object, view role, reviewer, decision, and
notes. Do not edit blob files. If a file must be replaced, import a new immutable
batch so that the archive hash, blob hashes, and review history remain traceable.

## 6. Manifest schema

`manifest.json` uses
`schema_version: relicscope-private-artwork-import-v1` and contains:

- batch and source-archive SHA-256 provenance without the source machine's absolute
  path;
- the exact limits used and aggregate counts;
- a security-check summary;
- one asset record per ZIP entry, including original/normalized paths, encoding
  decision, content hash, media facts, content-addressed path, candidate grouping,
  view-review state, and provenance/confidence;
- ignored macOS metadata paths and reasons.

This intake manifest is a staging record. Promotion into the governed RelicScope
reference library requires separate rights, source, calibration, expert-review,
counterfeit-control, and evaluation evidence described in
`REFERENCE_LIBRARY_DEPLOYMENT.md`.
