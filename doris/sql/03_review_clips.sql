-- Render with the same run ID as the search.
-- source_path is an original Hugging Face path, not an independent OSS MP4 URL.
-- Retrieve video_bytes by media_id with the pipeline.export_media helper.
-- The initial -2/+3 second window only locates the event; annotate a complete attempt.
-- next_reward/next_done are preserved source signals, not grasp-failure labels.
SELECT h.*, m.source_path, m.duration_s,
       GREATEST(0.0, h.timestamp_s - 2.0) AS review_start_s,
       LEAST(m.duration_s, h.timestamp_s + 3.0) AS review_end_s,
       f.task, f.observation_state, f.action, f.next_reward, f.next_done
FROM internal.droid100_analysis.grasp_hits h
JOIN droid100.`default`.media m ON h.media_id = m.media_id
JOIN droid100.`default`.frames f
  ON h.episode_index = f.episode_index AND h.frame_index = f.frame_index
WHERE h.run_id = '{{RUN_ID}}'
ORDER BY h.similarity DESC;

SELECT h.episode_index, f.frame_index, f.`timestamp`, f.observation_state, f.action
FROM internal.droid100_analysis.grasp_hits h
JOIN droid100.`default`.frames f ON h.episode_index = f.episode_index
WHERE h.run_id = '{{RUN_ID}}'
  AND f.`timestamp` BETWEEN h.timestamp_s - 2.0 AND h.timestamp_s + 3.0
ORDER BY h.episode_index, f.frame_index;
