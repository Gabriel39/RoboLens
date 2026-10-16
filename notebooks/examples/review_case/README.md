# Filled review walkthrough / 已填好的复核案例

These eight fictional records are ready to present in notebook chapters 06–08.
No user edits, live database connection, or label import is required.

这 8 条记录是虚构演示数据，页面已直接展示；无需填写、连接 Doris 或导入标签。

- `grasp_review.csv`: eight reviews / 8 条成败、质量、原因与片段标注。
- `episode_policy.csv`: eight policies / 8 条场景分组、数据集划分与训练历史。
- `grasp_hits.csv`: fictional retrieved hits and similarity scores / 虚构检索结果。
- `media.csv`: fictional video metadata (8 seconds each) / 虚构视频元数据，没有对应真实视频。

All IDs are in the fictional range 9001–9008 and use run `DEMO_REVIEW_CASE`.
Do not import these records into the real DROID-100 dataset or relabel them as human observations.
`label_source=synthetic_demo` explicitly identifies their origin.

The notebook helper `notebooks.review_case` evaluates the existing
`04_failure_distribution.sql` and `05_select_training_samples.sql` SELECT templates
against these fixtures in an isolated SQLite in-memory database. This verifies the
example's rules and calculations, not Doris integration or retrieval performance.

Expected results / 预期结果：

- 8 retrieved, 8 reviewed, 6 eligible known outcomes; 3 failures / 6 = 50%.
- 9003 (unknown) and 9006 (poor quality) are excluded from the denominator.
- 9004 (already used) and 9005 (test split) still count in review statistics but cannot be selected for training.
- 9007 shares scene and training use with 9001; 9001 has the higher similarity and is retained.
- Final candidates: 9001 (`bc_positive`), 9002 and 9008 (`failure_diagnosis_or_critic`).
- Every clip spans 2.0–4.5 seconds. These are selected candidates, not model training results.
