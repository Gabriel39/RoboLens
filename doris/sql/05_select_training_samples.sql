-- Episode policy must cover complete scene/session groups, including held-out episodes.
-- Success clips are behavior-cloning candidates; failures require a different objective.
-- Never use failed actions indiscriminately as correct behavior-cloning targets.
-- Keep one candidate per scene group and cap each object/scene/use bucket at 20.
-- Unknown membership is excluded by the inner join, not treated as untrained data.
-- The diagnostic query below should return no mixed-split scene groups.
SELECT scene_group, COUNT(DISTINCT split_assignment) AS split_count
FROM internal.droid100_analysis.episode_policy
GROUP BY scene_group HAVING COUNT(DISTINCT split_assignment) > 1;

WITH allowed_groups AS (
    SELECT scene_group FROM internal.droid100_analysis.episode_policy
    GROUP BY scene_group
    HAVING COUNT(DISTINCT split_assignment) = 1 AND MIN(split_assignment) = 'train'
), eligible AS (
    SELECT h.episode_index, h.sample_id, h.media_id, h.similarity,
           r.object_type, r.scene_type, r.failure_stage, r.failure_reason,
           r.clip_start_s, r.clip_end_s, p.scene_group,
           CASE WHEN r.outcome = 'success' THEN 'bc_positive'
                ELSE 'failure_diagnosis_or_critic' END AS training_use
    FROM internal.droid100_analysis.grasp_hits h
    JOIN internal.droid100_analysis.grasp_review r
      ON h.run_id = r.run_id AND h.sample_id = r.sample_id
    JOIN internal.droid100_analysis.episode_policy p ON h.episode_index = p.episode_index
    JOIN droid100.`default`.media m ON h.media_id = m.media_id
    JOIN allowed_groups g ON p.scene_group = g.scene_group
    WHERE h.run_id = '{{RUN_ID}}'
      AND r.reviewed = TRUE AND r.is_target_grasp = TRUE AND r.quality_ok = TRUE
      AND r.outcome IN ('success','failure')
      AND r.clip_start_s >= 0 AND r.clip_end_s > r.clip_start_s
      AND r.clip_end_s <= m.duration_s
      AND p.split_assignment = 'train' AND p.used_for_training = FALSE
      AND p.scene_group <> ''
), scene_dedup AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY object_type, scene_type, training_use, scene_group
        ORDER BY similarity DESC, episode_index
    ) AS group_rank FROM eligible
), quota AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY object_type, scene_type, training_use
        ORDER BY similarity DESC, episode_index
    ) AS bucket_rank FROM scene_dedup WHERE group_rank = 1
)
SELECT episode_index, sample_id, media_id, object_type, scene_type,
       training_use, failure_stage, failure_reason, scene_group,
       clip_start_s, clip_end_s, similarity
FROM quota WHERE bucket_rank <= 20
ORDER BY object_type, scene_type, training_use, similarity DESC;
