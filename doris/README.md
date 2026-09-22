# Doris queries and analysis

[English](README.md) | [简体中文](README.zh-CN.md)

This directory contains the SQL workflow for finding similar scenes after a failed grasp,
analyzing failure distributions, and selecting training candidates. Complete [ingestion](../pipeline/README.md)
first, then run the following commands from the project root.

## 1. Create the catalog and analysis tables

You need Doris FE/BE nodes that can access the same OSS bucket, a MySQL client, and a Doris build
supporting Lance Catalog and `vector_search()`. The [public 4.x documentation](https://doris.apache.org/docs/4.x/lakehouse/catalogs/lance-catalog/)
currently marks Lance Catalog as supported from 4.2; a “4.1” version label alone does not establish support.

```bash
mkdir -p work/config
cp doris/sql/01_setup.sql work/config/01_setup.sql
```

Edit warehouse, endpoint, region, and the `REPLACE_*` credentials in the copy to point to this
DROID-100 export. SQL does not expand `.env`; fill these values manually. The copy is under the
git-ignored `work/` directory. The example uses one replica; adjust `replication_num` for your cluster.

```bash
mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/config/01_setup.sql
```

Check that both `export_checkpoints.complete` and `export_index_status.complete` are true.
`SHOW INDEX` should show the cosine vector index on frame_embeddings. Empty audio tables need no
index. Run the initial setup only once; inspect existing catalogs/tables before considering any replacement.

| Location | Contents |
|---|---|
| `droid100.default.*` | Lance external tables on OSS: original data and vectors |
| `internal.droid100_analysis.grasp_hits` | Selected episodes and reference frames for each search run |
| `internal.droid100_analysis.grasp_review` | Human review labels and time intervals for complete attempts |
| `internal.droid100_analysis.episode_policy` | Scene groups, dataset splits, and training-use registry |

The project uses one fixed DROID-100 snapshot. Do not mix episode identifiers from other datasets
into these analysis tables.

## 2. Generate SQL with a real query vector

Read an exported frame's stored vector without loading the model again:

```bash
python -m doris.prepare_query \
  --dataset-root oss://YOUR_BUCKET/robotics/droid100 \
  --sample-id '0:observation.images.wrist_image_left:0' \
  --run-id droid100_demo_001 \
  --output-dir work/queries/droid100_demo_001
```

This is a connectivity demonstration frame, not a known failure sample. For an actual incident,
identify a failure keyframe and replace sample-id. Its format is `episode_index:camera:frame_index`.
With sampled exports, choose a frame that actually has an embedding.

For a newly captured image of a failed grasp:

```bash
python -m doris.prepare_query \
  --image work/reference/failed_grasp.jpg \
  --camera wrist \
  --device auto \
  --run-id failed_grasp_001 \
  --output-dir work/queries/failed_grasp_001
```

This branch downloads and uses the same SigLIP2 revision as ingestion to produce a normalized
768-dimensional vector. Choose wrist, exterior1, or exterior2 for camera, matching the image viewpoint.

The helper writes four SQL files (`02`–`05`) and `query.json`; it does not execute queries. Use a new
run-id and output directory whenever changing the reference or parameters. Existing results are not
automatically cleared. The commands below use the demonstration run-id; substitute the appropriate
directory for a new-image query.

```bash
mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/queries/droid100_demo_001/02_vector_search.sql

mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/queries/droid100_demo_001/03_review_clips.sql
```

Vector Search prefilters by camera, model revision, and episode range, and excludes the reference
episode. Candidates include both successes and failures rather than only failures. It retrieves up
to 2,000 frames, deduplicates by episode, and retains at most 50 episodes; fewer may be returned when
data or similar clips are insufficient. DROID-100 has only 100 episodes in total. Smaller `_distance`
means closer; `1 - _distance` is a displayed cosine similarity, not a success probability.
`nprobes=20` and `refine_factor=10` are starting values to tune against recall and latency. Moving
filters outside the TVF applies them after candidate generation and may leave fewer results.

## 3. Review clips and import real labels

`03_review_clips.sql` joins original videos, states, and actions, starting with a window two seconds
before and three seconds after the match. Expand it to a complete grasp attempt during review.
Do not label an entire episode containing multiple retries as one failed attempt.
Extract the original MP4 using a media_id from the results:

```bash
python -m pipeline.export_media \
  --dataset-root oss://YOUR_BUCKET/robotics/droid100 \
  --media-id '0:observation.images.wrist_image_left' \
  --output work/review/episode_000000_wrist.mp4
```

This extracts the whole video and verifies SHA256. Original videos are stored in a Lance binary
column; source_path is not an OSS MP4 URL.

```bash
mkdir -p work/labels
cp doris/templates/grasp_review.csv work/labels/grasp_review.csv
cp doris/templates/episode_policy.csv work/labels/episode_policy.csv
```

Templates contain headers only. Fill them with actual review records; the project supplies no
fabricated success/failure labels.

| Field | Rules |
|---|---|
| run_id / sample_id / episode_index | Use keys from this run's grasp_hits; do not renumber |
| reviewed / is_target_grasp / quality_ok | true/false: reviewed, belongs to the target grasp task, and usable quality |
| object_type / scene_type | Use consistent object and scene vocabularies; use unknown when uncertain |
| outcome | success / failure / unknown; uncertain outcomes stay out of the rate denominator |
| failure_stage / failure_reason | For example contact / slip; not_applicable may be used for successes |
| clip_start_s / clip_end_s | Start/end seconds of the complete attempt, within the video duration |
| label_source | Review source, such as human_review_v1 |
| scene_group | Actual collection scene/session; the same group must not span train/validation/test |
| split_assignment | train / validation / test |
| used_for_training | Whether already used for training; must come from the actual training registry |

episode_policy should cover the whole dataset being used, including held-out episodes, rather than
only retrieved training candidates. The source does not provide ready-made scene splits or training
history: your workflow must supply them. Unknown membership is excluded from selection. Keep CSV
columns in template order, use vocabulary values without commas or newlines, and use `\N` for nulls.

Import completed files using [Stream Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/).
`csv_with_names` skips the header. These commands use unique labels and prompt for a password:

```bash
curl --location-trusted -u "$DORIS_USER" \
  -H "label:droid100_review_$(date +%s)" \
  -H 'format:csv_with_names' -H 'column_separator:,' \
  -H 'strict_mode:true' -H 'max_filter_ratio:0' \
  -T work/labels/grasp_review.csv \
  "http://${DORIS_HOST}:${DORIS_HTTP_PORT}/api/droid100_analysis/grasp_review/_stream_load"

curl --location-trusted -u "$DORIS_USER" \
  -H "label:droid100_policy_$(date +%s)" \
  -H 'format:csv_with_names' -H 'column_separator:,' \
  -H 'strict_mode:true' -H 'max_filter_ratio:0' \
  -T work/labels/episode_policy.csv \
  "http://${DORIS_HOST}:${DORIS_HTTP_PORT}/api/droid100_analysis/episode_policy/_stream_load"
```

Check Status and loaded/filtered row counts in the returned JSON; HTTP 200 alone does not mean a
successful import. Fill header-only files before interpreting failure rates. The FE redirects to a
BE, which the client must also be able to reach. If the response is lost, check the original label's
status before retrying with a different label.

## 4. Analyze distributions and select training candidates

```bash
mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/queries/droid100_demo_001/04_failure_distribution.sql

mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/queries/droid100_demo_001/05_select_training_samples.sql
```

`04` first measures review coverage, then groups known outcomes by object/scene and failures by
stage/reason. These statistics describe the retrieved and reviewed cohort; they are not deployment-wide failure rates.

`05` first detects scene groups spanning splits, then excludes those groups, already trained
samples, poor-quality samples, and invalid intervals. Within each object/scene/training-use bucket,
it deduplicates by scene group and selects at most 20 clips. The output is a traceable list of
episode, sample, media, time interval, and training use. It does not train a model or automatically
update the training registry. Success clips are marked `bc_positive`; failures are marked
`failure_diagnosis_or_critic`. Do not use failed actions directly as correct behavior-cloning targets.

See the [case study](../docs/CASE_STUDY.md) for the business context and
[validation record](../docs/VALIDATION.md) for what has been tested.
