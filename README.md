# RoboLens

[English](README.md) | [简体中文](README.zh-CN.md)

**Explore robot behavior through data.**

DROID-100 → OSS Lance → Doris Vector Search

RoboLens downloads and processes DROID-100 in Python, generates embeddings, writes Lance datasets
to OSS, and builds vector indexes. Doris uses `vector_search()` to retrieve similar clips, analyze
grasp failures, and select training candidates.

The [Jupyter walkthrough](notebooks/README.md) includes an overview and eight step diagrams.
Run ingestion, Vector Search, video review, and failure analysis interactively, or export an HTML
presentation with code hidden. Open the [English preview](notebooks/droid100_preview.en.html)
without installing Python. The preview contains no fabricated business results.

## Dataset version and size

The source is [`lerobot/droid_100`](https://huggingface.co/datasets/lerobot/droid_100), pinned to
commit `78f887947f976d85dd04594bcbd0b05c29893349` from the official **v2.1 tag**.
The repository's `main` branch has moved to v3.0. This project deliberately uses v2.1, which stores
separate Parquet and MP4 files per episode; it does not interpret v3 shared files as per-episode files.

| Item | Count in the pinned version |
|---|---:|
| Episodes | 100 |
| State/action time steps | 32,212 |
| Task text entries | 47 |
| MP4 videos | 300, from three cameras |
| Frame rate | 15 fps |
| observation.state / action | Both 7-dimensional float32 |
| Frame embeddings, stride=1 | 96,636 vectors, 768 dimensions |
| Frame embeddings, stride=15 | 6,591 vectors, 768 dimensions |
| Aggregated video embeddings | 300 vectors, 768 dimensions |
| Audio embeddings | Normally 0; source videos have no audio tracks |

In addition to state/action, the pipeline preserves `next.reward` and `next.done`, renaming them to
`next_reward` and `next_done`. They are not automatically success/failure labels for a grasp attempt.
See [the schema reference](docs/SCHEMA.md).

## Project layout

```text
RoboLens/
  README.md
  requirements.txt
  requirements-dev.txt
  requirements-notebook.txt
  .env.example
  pipeline/
    README.md
    export_droid.py
    export_media.py
  doris/
    README.md
    prepare_query.py
    sql/
      01_setup.sql
      02_vector_search.sql
      03_review_clips.sql
      04_failure_distribution.sql
      05_select_training_samples.sql
    templates/
      grasp_review.csv
      episode_policy.csv
  docs/
    SCHEMA.md
    CASE_STUDY.md
    VALIDATION.md
  notebooks/
    README.md
    droid100_end_to_end.ipynb
    droid100_preview.html
    demo_support.py
    assets/
  tests/
    test_export.py
```

## 1. Install

Run from the project root. Linux and Python 3.11/3.12 are recommended. A GPU accelerates embedding
inference, but CPU inference is also supported. Lance indexes are built on the CPU by default.

```bash
git clone https://github.com/Gabriel39/RoboLens.git
cd RoboLens
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Install the PyTorch wheel appropriate for your CUDA/CPU environment. Edit `.env` to provide OSS
credentials and the endpoint, then load its variables:

```bash
set -a
source .env
set +a
```

Credentials are read from the environment and are not written to the export manifest. The script
does not create buckets: use an existing OSS bucket and credentials with read, list, and write
permissions. Refresh expired STS tokens in the environment before resuming.

## 2. Validate two episodes, then export all 100

Use separate output prefixes for the smoke test and full export: the episode range is part of the
export configuration.

```bash
python -m pipeline.export_droid \
  --output oss://YOUR_BUCKET/robotics/droid100_smoke \
  --work-dir work/smoke \
  --max-episodes 2 \
  --frame-stride 15
```

Export all episodes with an embedding for every frame:

```bash
python -m pipeline.export_droid \
  --output oss://YOUR_BUCKET/robotics/droid100 \
  --work-dir work/full \
  --device auto \
  --frame-stride 1
```

Omitting `--max-episodes` exports all 100 episodes. Use `--frame-stride 15` to reduce embedding
computation while preserving all original video bytes and state/action rows. Reduce
`--embedding-batch-size` if GPU memory is insufficient. For local validation, set `--output` to
`work/local-output`; this does not require OSS credentials.

The output contains six business datasets and three operational datasets:

```text
oss://YOUR_BUCKET/robotics/droid100/
  frames.lance/
  episodes.lance/
  media.lance/
  frame_embeddings.lance/
  audio_embeddings.lance/
  metadata.lance/
  export_manifest.lance/
  export_checkpoints.lance/
  export_index_status.lance/
```

Original MP4 files are stored in `media.video_bytes`. Index files live in the corresponding dataset's
`_indices/` directory. These are not standalone OSS MP4 URLs. Vector Search does not require copying
the vectors into a Doris internal table.

## 3. Resume exports and build indexes

After an interrupted export, keep the same episode range, sampling stride, and output prefix,
and add `--resume`:

```bash
python -m pipeline.export_droid \
  --output oss://YOUR_BUCKET/robotics/droid100 \
  --work-dir work/full \
  --frame-stride 1 \
  --resume
```

For a complete DROID-100 export that only needs index creation or recovery:

```bash
python -m pipeline.export_droid \
  --output oss://YOUR_BUCKET/robotics/droid100 \
  --work-dir work/full \
  --index-only
```

The default is a cosine IVF_PQ index. Vector tables with fewer than 256 rows automatically use
IVF_FLAT; empty audio tables are skipped. Committed indexes are reused, so index recovery does not
repeat downloads or embedding computation. **Do not reuse an earlier `cadene/droid` output prefix
or source-root**: the source, state dimensions, and configuration differ. See the
[pipeline guide](pipeline/README.md) for parameters, models, and recovery rules.

## 4. Query and analyze with Doris

Follow the [Doris guide](doris/README.md):

1. Configure the OSS bucket and credentials in `01_setup.sql`, then create the Lance Catalog and analysis tables.
2. Generate SQL from a real reference frame; the helper inserts the 768-dimensional query vector.
3. Run Vector Search and obtain the clip review list.
4. Import actual human review labels and training registry data, then run failure analysis and sample selection.

Generate a connectivity demonstration query:

```bash
python -m doris.prepare_query \
  --dataset-root oss://YOUR_BUCKET/robotics/droid100 \
  --sample-id '0:observation.images.wrist_image_left:0' \
  --run-id droid100_demo_001 \
  --output-dir work/queries/droid100_demo_001
```

The reference frame exists, but it is only a demonstration: **it is not asserted to be a failed grasp**.
For actual analysis, replace it with a manually identified failure keyframe. You can also use `--image`
with a newly captured local image; the helper uses the same SigLIP2 revision. It only generates SQL:
it does not connect to Doris or synthesize outcome labels.

Use a Doris build with Lance Catalog and `vector_search()` support. The public 4.x documentation
currently marks Lance Catalog as supported from 4.2; check your actual build's capabilities.
The example does not calculate distances manually to sort the entire table.

## 5. Tests and validation scope

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover real media encoding/decoding, numeric and byte fidelity, indexing and recovery,
and SQL generation. Models are explicit test doubles; indexes use the real
Lance SDK. The real DROID-100 episode 0 was also validated. See the [validation record](docs/VALIDATION.md)
for evidence and untested boundaries, and the [case study](docs/CASE_STUDY.md) for the business workflow.
The code has not been run against your OSS/Doris environment.

## Official references

- [Pinned dataset version](https://huggingface.co/datasets/lerobot/droid_100/tree/78f887947f976d85dd04594bcbd0b05c29893349)
- [Doris Lance Catalog / Vector Search](https://doris.apache.org/docs/4.x/lakehouse/catalogs/lance-catalog/)
- [Lance OSS configuration](https://lance.org/guide/object_store/#alicloud-object-storage-service-configuration)
- [SigLIP2](https://huggingface.co/google/siglip2-base-patch16-224)
- [CLAP](https://huggingface.co/laion/clap-htsat-unfused)
