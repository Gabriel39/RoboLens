# Lance 输出 Schema

[English](SCHEMA.md) | [简体中文](SCHEMA.zh-CN.md)

由固定 DROID-100 v2.1 的真实第 0 集 Parquet 和导出代码生成；保留原始数值类型。

除原始 metadata 文件表外，业务表的 episode 关联键为 `episode_index`，视频键为 `media_id`，帧向量通过 `index` 或 `episode_index + frame_index` 关联状态。

## frames.lance

| 字段 | Arrow 类型 |
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

| 字段 | Arrow 类型 |
|---|---|
| `episode_index` | `int64` |
| `length` | `int64` |
| `tasks` | `list<item: string>` |
| `metadata_json` | `string` |

## media.lance

| 字段 | Arrow 类型 |
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

| 字段 | Arrow 类型 |
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

| 字段 | Arrow 类型 |
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

| 字段 | Arrow 类型 |
|---|---|
| `path` | `string` |
| `data` | `large_binary` |

这里严格只有 path 和 data：每行保存一个原始 meta 文件，不直接提供 episode_index。
脚本另将 episodes.jsonl 解析为 episodes 表，将 tasks.jsonl 的任务文字附加到 frames，
因此日常查询不必从 metadata 的二进制 JSON 中反复提取 episode。
本次固定版本有四个 meta 文件：info.json、tasks.jsonl、episodes.jsonl、episodes_stats.jsonl。
即使只导出一部分 episode，metadata 仍保留源版本的完整元数据，不能用它的 total_frames
当作部分导出的实际行数。

## 运行记录

| Dataset | 字段 |
|---|---|
| `export_manifest` | `config_json: string`；包括源/模型版本、采样参数、字段映射 |
| `export_checkpoints` | `next_episode_offset: int64`、`versions_json: string`、`complete: bool`、`fingerprint: string` |
| `export_index_status` | `source_fingerprint: string`、`indexes_json: string`、`complete: bool` |

`next_reward`、`next_done` 原样保留，不能将 episode 终止自动解释为抓取失败，也不能据此替代片段级人工复核。
`state/action` 都是 7 维，源元数据仅提供 motor_0…motor_6 名称；不凭名称擅自推断每维的物理单位。
`video_bytes` 使用普通 large_binary，不使用 Doris 尚不支持的 Blob v2 extension。
