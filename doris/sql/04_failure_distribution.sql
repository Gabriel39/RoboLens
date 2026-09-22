-- Import real human-reviewed labels before running this analysis.
-- Unknown/unreviewed outcomes must not count as success.
-- Percentages describe this retrieved and reviewed cohort, not deployment failure rates.
-- Label one complete grasp attempt, not the entire episode with multiple retries.
SELECT COUNT(*) AS retrieved_episodes,
       SUM(CASE WHEN r.reviewed = TRUE THEN 1 ELSE 0 END) AS reviewed_clips,
       SUM(CASE WHEN r.reviewed = TRUE AND r.is_target_grasp = TRUE
                     AND r.quality_ok = TRUE AND r.outcome IN ('success','failure')
                THEN 1 ELSE 0 END) AS eligible_known_outcomes
FROM internal.droid100_analysis.grasp_hits h
LEFT JOIN internal.droid100_analysis.grasp_review r
  ON h.run_id = r.run_id AND h.sample_id = r.sample_id
WHERE h.run_id = '{{RUN_ID}}';

SELECT r.object_type, r.scene_type,
       COUNT(*) AS reviewed_known_clips,
       SUM(CASE WHEN r.outcome = 'failure' THEN 1 ELSE 0 END) AS failed_clips,
       ROUND(100.0 * SUM(CASE WHEN r.outcome = 'failure' THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0), 2) AS failure_pct
FROM internal.droid100_analysis.grasp_hits h
JOIN internal.droid100_analysis.grasp_review r
  ON h.run_id = r.run_id AND h.sample_id = r.sample_id
WHERE h.run_id = '{{RUN_ID}}'
  AND r.reviewed = TRUE AND r.is_target_grasp = TRUE AND r.quality_ok = TRUE
  AND r.outcome IN ('success','failure')
GROUP BY r.object_type, r.scene_type
ORDER BY failed_clips DESC;

SELECT r.failure_stage, r.failure_reason, COUNT(*) AS failed_clips
FROM internal.droid100_analysis.grasp_hits h
JOIN internal.droid100_analysis.grasp_review r
  ON h.run_id = r.run_id AND h.sample_id = r.sample_id
WHERE h.run_id = '{{RUN_ID}}'
  AND r.reviewed = TRUE AND r.is_target_grasp = TRUE AND r.quality_ok = TRUE
  AND r.outcome = 'failure'
GROUP BY r.failure_stage, r.failure_reason
ORDER BY failed_clips DESC;
