# Validation record

[English](VALIDATION.md) | [简体中文](VALIDATION.zh-CN.md)

Validation date: 2026-09-22. Environment: Linux, Python 3.12, pylance 11.0.0, PyArrow 22.0.0, PyAV 16.1.0.

## Automated tests

The original pipeline/query suite passed **24 tests**, run with `python -m pytest -q` from the project root.

Coverage includes original state/action/reward/done values and types, video bytes and SHA256,
sampling and timestamp alignment, audio windowing, real Lance reads/writes and indexes, index
parameters and coverage, partial-write recovery, index preservation when resuming complete exports,
rejection of external data changes and incompatible source layouts/dimensions, reference vector reads,
SQL template rendering, input validation, video extraction, and overwrite protection.

Tests use real PyAV and Lance SDKs with explicit embedding model doubles, so they do not establish
semantic retrieval quality. The production CLI has no fake-embedding mode. The SDK emits list_indices
deprecation warnings; the API remains functional in the pinned version.

## Real DROID-100 file validation

Four meta files plus episode 0's real Parquet and three AV1 MP4 files were downloaded from commit
`78f887947f976d85dd04594bcbd0b05c29893349`. Metadata confirms 100 episodes, 32,212 time steps,
47 tasks, 15 fps, three cameras, and seven-dimensional state/action. Episode 0's task is
`Put the marker in the pot`.

A local export of episode 0 at frame-stride=15 produced:

| Dataset | Validated local rows |
|---|---:|
| frames | 166 |
| episodes | 1 |
| media | 3 |
| frame_embeddings | 36 |
| audio_embeddings | 0 |
| metadata | 4 |

All original columns and types were compared with the source Parquet, and complete MP4 bytes were
compared with the originals. Real AV1 video decoded successfully. Cosine IVF_FLAT indexes were
built and checked on frame and video vectors. Because the sample was small, the script switched
from default IVF_PQ as designed. The empty audio table was skipped.

The CLI also generated query files `02`–`05` from the stored vector of episode 0's first frame,
and the media helper extracted a wrist MP4 with verified SHA256. This real-file validation still
used a model double; those test vectors must not be used for business retrieval.

## Not yet validated

- The complete 100-episode export was not downloaded/run. Full counts come from pinned metadata; embedding counts are calculated from episode lengths and sampling rules.
- Full SigLIP2/CLAP weights were not loaded for local inference. GPU throughput and semantic recall were not evaluated.
- No connection to your S3 / OSS was made; credentials, networking, and object-store throughput remain untested.
- SQL was not executed against Doris. It follows the official Lance Catalog/Vector Search and Stream Load documentation.
- No real failure labels were generated or assumed, and no actual failure rates or training gains were reported.

Start with a two-episode validation using a separate output prefix, as described in the root README,
then proceed to a full export and Doris queries. The package excludes model-double Lance data,
model caches, original videos, credentials, and fabricated review labels.

## Jupyter walkthrough validation

Each notebook contains 34 cells, one overview diagram, and eight step SVG diagrams. Preview mode
was executed in a separate Python kernel without errors, then exported to HTML with code inputs
hidden. The nine embedded diagrams were checked in a browser, including page and diagram text layout.

Notebook helper tests cover SQL semicolons inside literals, parameterized label writes, rejection
of invalid run/episode labels and boolean/time/outcome values before writes, and rejection of
incomplete split registry coverage. With notebook dependencies installed, the full suite passes
**43 tests**. Optional notebook tests can be skipped when only base development dependencies are installed.
The HTML is a process walkthrough, not an S3 / OSS/Doris execution report or fabricated search/failure result.

## S3 protocol validation

New tests run Moto 5.2.3 as a local S3 HTTP service and use the real Lance SDK to export two
synthetic episodes with media and state data. They verify six business tables, checkpoints, index
files, indexed retrieval, index preservation on resume, reference-vector reads, and byte-exact MP4
extraction. Embeddings remain explicit model doubles. Configuration tests cover AWS regions and
temporary tokens, endpoint/request style, OSS isolation, and notebook catalog mapping for AWS,
AWS China regions, and compatible endpoints. This does not establish compatibility with a real
AWS account, a deployed MinIO service, or Doris. Both notebook previews were executed again and
exported to HTML. Install `requirements-dev.txt` for tests and `requirements-notebook.txt` for the
optional notebook helper tests.
