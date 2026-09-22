-- Render this template with python -m doris.prepare_query.
-- Vector Search runs in Lance and reuses the cosine index built by the pipeline.
-- Use a new run ID for each query; this INSERT does not delete stale run results.
-- Prefilter before candidate generation; do not prefilter to failure-only rows.
-- Retrieve up to 2000 frames, then deduplicate to at most 50 other episodes.
-- Increase top_k if nearby frames consume too much of the candidate budget.
-- nprobes/refine_factor are example query parameters, not universal optima.
-- 1 - _distance is a display similarity only because metric is cosine.
INSERT INTO internal.droid100_analysis.grasp_hits
SELECT * FROM (
    WITH candidates AS (
        SELECT episode_index, sample_id, media_id, frame_index,
               `timestamp` AS timestamp_s, _distance
        FROM vector_search(
            "table" = "droid100.default.frame_embeddings",
            "column" = "embedding",
            "query_vector" = "{{QUERY_VECTOR_JSON}}",
            "top_k" = "2000",
            "metric" = "cosine",
            "nprobes" = "20",
            "refine_factor" = "10",
            "filter" = "camera = '{{CAMERA}}' AND model = 'google/siglip2-base-patch16-224' AND model_revision = '75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2' AND episode_index >= 0 AND episode_index < 100 AND episode_index != {{REFERENCE_EPISODE}}",
            "use_index" = "true"
        )
    ), ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY episode_index ORDER BY _distance ASC, frame_index
        ) AS rn
        FROM candidates
    )
    SELECT '{{RUN_ID}}', episode_index, sample_id, media_id,
           frame_index, timestamp_s, 1 - _distance AS similarity
    FROM ranked WHERE rn = 1
    ORDER BY _distance ASC, episode_index LIMIT 50
) candidate_rows;

SHOW INDEX FROM droid100.`default`.frame_embeddings;
