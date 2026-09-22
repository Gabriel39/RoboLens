# Lance output schema

[English](SCHEMA.md) | [简体中文](SCHEMA.zh-CN.md)

Generated from the real episode 0 Parquet file of pinned DROID-100 v2.1 and the exporter. Original numeric types are preserved.

Except for the raw metadata-file table, business tables join on `episode_index`. Videos use `media_id`; frame vectors join states through `index` or `(episode_index, frame_index)`.

## frames.lance

| Field | Arrow type |
|---|---|
| `observation_state` | `fixed_size_list<element: float>[7]` |
| `action` | `fixed_size_list<element: float>[7]` |
| `timestamp` | `float` |
| `episode_index` | `int64` |
| `frame_index` | `int64` |
| `next_reward` | `float` |
| `next_done` | `bool` |
| `index` | `int64` |
| `task_index` | `int64` |
| `task` | `string` |
| `source_parquet` | `string` |

## episodes.lance

| Field | Arrow type |
|---|---|
| `episode_index` | `int64` |
| `length` | `int64` |
| `tasks` | `list<item: string>` |
| `metadata_json` | `string` |

## media.lance

| Field | Arrow type |
|---|---|
| `media_id` | `string` |
| `episode_index` | `int64` |
| `camera` | `string` |
| `source_path` | `string` |
| `sha256` | `string` |
| `byte_size` | `int64` |
| `frame_count` | `int64` |
| `fps` | `double` |
| `duration_s` | `double` |
| `audio_stream_count` | `int32` |
| `video_bytes` | `large_binary` |
| `video_embedding` | `fixed_size_list<item: float>[768]` |
| `embedding_frame_count` | `int64` |
| `embedding_model` | `string` |
| `embedding_revision` | `string` |

## frame_embeddings.lance

| Field | Arrow type |
|---|---|
| `sample_id` | `string` |
| `index` | `int64` |
| `episode_index` | `int64` |
| `frame_index` | `int64` |
| `timestamp` | `double` |
| `video_timestamp` | `double` |
| `camera` | `string` |
| `media_id` | `string` |
| `embedding` | `fixed_size_list<item: float>[768]` |
| `model` | `string` |
| `model_revision` | `string` |

## audio_embeddings.lance

| Field | Arrow type |
|---|---|
| `sample_id` | `string` |
| `episode_index` | `int64` |
| `media_id` | `string` |
| `camera` | `string` |
| `stream_index` | `int32` |
| `start_s` | `double` |
| `end_s` | `double` |
| `sample_rate` | `int32` |
| `embedding` | `fixed_size_list<item: float>[512]` |
| `model` | `string` |
| `model_revision` | `string` |

## metadata.lance

| Field | Arrow type |
|---|---|
| `path` | `string` |
| `data` | `large_binary` |

This table has exactly two columns, path and data. Each row preserves one original meta file;
it does not directly expose episode_index. The exporter separately parses episodes.jsonl into the
episodes table and adds task text from tasks.jsonl to frames, so ordinary queries do not need to
repeatedly extract episode data from the binary JSON files.
The pinned version has four meta files: info.json, tasks.jsonl, episodes.jsonl, and episodes_stats.jsonl.
Even a partial episode export preserves the source version's complete metadata. Its total_frames
must not be interpreted as the actual row count of a partial export.

## Operational datasets

| Dataset | Fields |
|---|---|
| `export_manifest` | `config_json: string`; source/model versions, sampling parameters, and field mapping |
| `export_checkpoints` | `next_episode_offset: int64`, `versions_json: string`, `complete: bool`, `fingerprint: string` |
| `export_index_status` | `source_fingerprint: string`, `indexes_json: string`, `complete: bool` |

`next_reward` and `next_done` are preserved unchanged. Episode termination is not automatically a
grasp failure and does not replace clip-level human review. Both state/action have seven dimensions;
the source only names motor_0 through motor_6, which does not establish physical units for each value.
`video_bytes` uses ordinary large_binary, not the Blob v2 extension unsupported by Doris.
