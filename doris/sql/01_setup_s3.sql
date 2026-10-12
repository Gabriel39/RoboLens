-- Configure S3 access and create review/result tables.
-- Replace every REPLACE_* placeholder before execution.
-- Run only after the export and index stages have completed.
-- replication_num=1 is for a single-node demo; adjust for your cluster.
-- This project uses one fixed DROID-100 source snapshot per catalog/database.
CREATE CATALOG {{CATALOG}} PROPERTIES (
    "type" = "lance",
    "lance.catalog.type" = "filesystem",
    "warehouse" = "s3://REPLACE_BUCKET/robotics/droid100",
    "fs.s3.support" = "true",
    "s3.endpoint" = "https://REPLACE_BUCKET.s3.us-east-1.amazonaws.com",
    "use_path_style" = "false",
    "s3.region" = "us-east-1",
    "s3.access_key" = "REPLACE_ACCESS_KEY",
    "s3.secret_key" = "REPLACE_SECRET_KEY"
);
SHOW TABLES FROM {{SOURCE_DATABASE}};
DESC {{SOURCE_FRAME_EMBEDDINGS}};

SELECT next_episode_offset, complete
FROM {{SOURCE_EXPORT_CHECKPOINTS}}
ORDER BY next_episode_offset DESC LIMIT 1;
SELECT complete, indexes_json
FROM {{SOURCE_EXPORT_INDEX_STATUS}};
SHOW INDEX FROM {{SOURCE_FRAME_EMBEDDINGS}};

CREATE DATABASE IF NOT EXISTS {{ANALYSIS_DATABASE}};

CREATE TABLE {{ANALYSIS_GRASP_HITS}} (
    run_id VARCHAR(64) NOT NULL,
    episode_index BIGINT NOT NULL,
    sample_id VARCHAR(256) NOT NULL,
    media_id VARCHAR(256) NOT NULL,
    frame_index BIGINT NOT NULL,
    timestamp_s DOUBLE NOT NULL,
    similarity DOUBLE NOT NULL
)
UNIQUE KEY(run_id, episode_index)
DISTRIBUTED BY HASH(episode_index) BUCKETS 8
PROPERTIES ("replication_num"="1", "enable_unique_key_merge_on_write"="true");

CREATE TABLE {{ANALYSIS_GRASP_REVIEW}} (
    run_id VARCHAR(64) NOT NULL,
    sample_id VARCHAR(256) NOT NULL,
    episode_index BIGINT NOT NULL,
    reviewed BOOLEAN NOT NULL,
    is_target_grasp BOOLEAN NOT NULL,
    quality_ok BOOLEAN NOT NULL,
    object_type VARCHAR(64),
    scene_type VARCHAR(64),
    outcome VARCHAR(16),
    failure_stage VARCHAR(64),
    failure_reason VARCHAR(128),
    clip_start_s DOUBLE,
    clip_end_s DOUBLE,
    label_source VARCHAR(64)
)
UNIQUE KEY(run_id, sample_id)
DISTRIBUTED BY HASH(sample_id) BUCKETS 8
PROPERTIES ("replication_num"="1", "enable_unique_key_merge_on_write"="true");

CREATE TABLE {{ANALYSIS_EPISODE_POLICY}} (
    episode_index BIGINT NOT NULL,
    scene_group VARCHAR(128) NOT NULL,
    split_assignment VARCHAR(16) NOT NULL,
    used_for_training BOOLEAN NOT NULL
)
UNIQUE KEY(episode_index)
DISTRIBUTED BY HASH(episode_index) BUCKETS 8
PROPERTIES ("replication_num"="1", "enable_unique_key_merge_on_write"="true");
