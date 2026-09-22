# Ingestion, processing, and indexing

[English](README.md) | [简体中文](README.zh-CN.md)

Run `python -m pipeline.export_droid` from the project root. See the [root README](../README.md)
for configuration and smoke-test/full-export commands. This directory handles source data to S3 / OSS Lance;
it does not connect to Doris.

See the [storage guide](../docs/STORAGE.md) for S3. Replace the `oss://` examples below with your `s3://` paths; use `01_setup_s3.sql` for Doris. The notebook selects the provider from `NOTEBOOK_DATASET_ROOT`.

## Processing steps

1. Read the pinned `lerobot/droid_100` v2.1 commit and all `meta/` files.
2. Download each episode's Parquet and three AV1 MP4 files; validate frame counts, episode/global indexes, and timestamps.
3. Preserve source numeric types and all original columns. Replace dots in field names with underscores and add task text and source paths.
4. Decode images and generate sampled embeddings. Preserve original MP4 bytes, SHA256 hashes, and aggregated video vectors.
5. Stage a batch locally as Arrow IPC, commit each Lance table, then commit the batch checkpoint.
6. Validate final row counts, build cosine indexes for nonempty vector tables, verify index coverage, and publish readiness status.

## Models and vectors

| Data | Model | Vector | Notes |
|---|---|---|---|
| Video frames | `google/siglip2-base-patch16-224` | 768-dimensional float32, L2-normalized | Every frame by default; pinned model revision |
| Entire video | Average sampled frame vectors, then normalize again | 768 dimensions | Does not encode action order |
| Audio track | `laion/clap-htsat-unfused` | 512 dimensions, L2-normalized | Mono 48 kHz, one window per 10 seconds |

Downloaded models run locally; no online embedding API is called. Original DROID-100 videos have no
audio tracks, so CLAP is not downloaded/loaded and the audio table has 0 rows. The script still checks
actual streams and processes audio if present. State/action records retain their original seven
values and do not pass through an embedding model.

## Common parameters

| Parameter | Default | Meaning |
|---|---|---|
| `--output` | Required | `s3://bucket/prefix`, `oss://bucket/prefix`, or a local directory |
| `--work-dir` | `./work` | Model cache, source metadata, and batch temporary files |
| `--source-root` | None | Downloaded DROID-100 v2.1 root containing meta/data/videos |
| `--max-episodes` | Unlimited | Episode count limit; combines with start-episode to define the range |
| `--start-episode` | 0 | First episode |
| `--frame-stride` | 1 | Embedding sampling only; 15 is about one frame per second |
| `--embedding-batch-size` | 32 | Image inference batch size; reduce if GPU memory is insufficient |
| `--episodes-per-commit` | 16 | Batch size for staging, committing, and recomputation after failure |
| `--device` | auto | Automatic CUDA/CPU, or explicit cpu/cuda/cuda:0 |
| `--timestamp-tolerance` | 0.04 seconds | Maximum difference between video and state timestamps |
| `--resume` | Off | Resume the current export and continue indexing |
| `--index-only` | Off | Index a complete export without reading HF source files or models |
| `--skip-indexes` | Off | Export data now and build indexes later |
| `--index-type` | IVF_PQ | IVF_FLAT is also supported; automatic IVF_FLAT below 256 rows |
| `--index-target-partition-size` | 8192 | Target vector count per IVF partition |
| `--index-num-sub-vectors` | 64 | PQ subvectors; must divide both 768 and 512 |
| `--index-shuffle-batches` | 32 | Shuffle work batches, not a total memory limit |

A local source may still require model downloads. Keep source files unchanged during export and
recovery. `--revision` can explicitly select another v2.1 commit, but v3's shared-file layout is not
supported. The default is a fixed source commit; avoid replacing it with a moving branch.

## Indexes and recovery

Indexed columns are `frame_embeddings.embedding`, `media.video_embedding`, and nonempty
`audio_embeddings.embedding`. They live directly in the S3 / OSS Lance datasets.

IVF_PQ defaults to 64 subvectors and the SDK's default 8-bit codebook. Fewer than 256 rows cannot train
that codebook, so the script switches to IVF_FLAT. Index parameters are included in the name digest;
keep the same parameters after starting. The script does not silently replace a different index.
Index training uses the CPU by default, independently of the image inference `--device` setting.

Only one writer may own an output prefix. The working-directory file lock prevents duplicate runs
on the same machine and directory; it is not a distributed lock. An interrupted data stage rolls
back to the last complete checkpoint to avoid partial batches or duplicates across tables. After
data completion, only index metadata may change; `--resume` preserves successfully committed indexes.
An uncommitted index must be rebuilt. If a commit succeeds but the client disconnects, the next run
discovers and reuses the index.

`export_checkpoints.complete` means data is complete. `export_index_status.complete` means every
nonempty vector table has a built and verified index. Empty audio tables are recorded as empty.
Failed indexing never publishes success. External data edits or compaction change fragments and
cause recovery to refuse to continue. Retain checkpoint-referenced versions until the entire
workflow is complete. The script does not compact data or clean up old versions automatically.

Model caches are retained; batch temporary files are removed on normal exit. After confirming the
process has stopped, you may remove `work/batch-*` directories left by a power failure. Reserve disk
and memory for vectors, indexes, models, and staging. A GPU is not required for the storage export itself.

## Extract original video for review

```bash
python -m pipeline.export_media \
  --dataset-root oss://YOUR_BUCKET/robotics/droid100 \
  --media-id '0:observation.images.wrist_image_left' \
  --output work/review/episode0-wrist.mp4
```

The helper reads one MP4 by media_id, verifies SHA256, writes it locally, and refuses to overwrite an
existing file. Align video time using `frame_embeddings.video_timestamp` and the source `timestamp`.
This helper neither trims nor re-encodes the video.
