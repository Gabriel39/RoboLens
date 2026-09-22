# Doris 查询与分析

[English](README.md) | [简体中文](README.zh-CN.md)

本目录对应“抓取失败后找相似场景、分析失败分布、筛选训练候选”的完整 SQL 流程。
先完成 [数据导入](../pipeline/README.zh-CN.md)，再从项目根目录执行以下命令。

## 1. 建立 Catalog 和分析表

需要可以访问同一个 OSS bucket 的 Doris FE/BE、MySQL 客户端，以及支持 Lance Catalog
和 `vector_search()` 的 Doris 构建。当前[官方 4.x 文档](https://doris.apache.org/docs/4.x/lakehouse/catalogs/lance-catalog/)
标注 Lance Catalog 从 4.2 起支持，不能仅凭“4.1”版本号假定功能存在。

```bash
mkdir -p work/config
cp doris/sql/01_setup.sql work/config/01_setup.sql
```

编辑副本中的 warehouse、endpoint、region 和 `REPLACE_*` 凭证；它们必须指向本次
DROID-100 导出。SQL 不会展开 `.env`，需要手动填入。该副本位于 git 忽略的 `work/` 下。
示例使用单副本配置，多 BE 环境按实际要求调整 `replication_num`。

```bash
mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/config/01_setup.sql
```

确认 `export_checkpoints.complete` 和 `export_index_status.complete` 均为 true。
`SHOW INDEX` 应能看到 frame_embeddings 的 cosine 向量索引。空音频表不需要索引。
初次建表脚本只运行一次；已有同名 Catalog/表时先核对其来源，不要直接删除重建。

| 位置 | 保存内容 |
|---|---|
| `droid100.default.*` | OSS 上的 Lance 外表，原始数据和向量 |
| `internal.droid100_analysis.grasp_hits` | 每次搜索选出的 episode 和参考帧 |
| `internal.droid100_analysis.grasp_review` | 人工复核结果和完整尝试的时间区间 |
| `internal.droid100_analysis.episode_policy` | 场景分组、数据切分和训练使用登记 |

该项目固定一份 DROID-100 快照。不要把其他数据集的 episode 编号混入这些分析表。

## 2. 生成包含真实查询向量的 SQL

已有导出帧可直接读取向量，不需要再次加载模型：

```bash
python -m doris.prepare_query \
  --dataset-root oss://YOUR_BUCKET/robotics/droid100 \
  --sample-id '0:observation.images.wrist_image_left:0' \
  --run-id droid100_demo_001 \
  --output-dir work/queries/droid100_demo_001
```

这是连通性演示帧，不是已知的失败样本。真实任务应先定位失败关键帧，再替换 sample-id。
格式为 `episode_index:camera:frame_index`；采样导出时只能选已生成向量的帧。

对于机器人新采集的失败图片：

```bash
python -m doris.prepare_query \
  --image work/reference/failed_grasp.jpg \
  --camera wrist \
  --device auto \
  --run-id failed_grasp_001 \
  --output-dir work/queries/failed_grasp_001
```

此分支会下载并使用与导出阶段相同 revision 的 SigLIP2，输出归一化 768 维向量。
camera 可选 wrist、exterior1、exterior2，应选最接近图片视角的一路。

工具生成 `02`–`05` 四个 SQL 和 `query.json`，只写文件，不执行查询。
每次换参考图或参数应使用新的 run-id 和输出目录；已有结果不会被自动清理。
以下命令使用演示 run-id，新图片流程替换为对应目录即可。

```bash
mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/queries/droid100_demo_001/02_vector_search.sql

mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/queries/droid100_demo_001/03_review_clips.sql
```

Vector Search 使用预过滤限定相机、模型版本、episode 范围，并排除参考 episode。
候选包含成功与失败，避免一开始就只检索失败样本。先取最多 2,000 帧，再按 episode 去重，
最多保留 50 集；数据量或相似片段不足时结果更少。DROID-100 总共只有 100 集。
`_distance` 越小越近；`1 - _distance` 只是 cosine 相似度展示，不是成功概率。
`nprobes=20`、`refine_factor=10` 是可调起点，应按召回率和延迟实测。
把过滤条件移到 TVF 外层会变成候选返回后的过滤，可能使结果数量不足。

## 3. 复核并导入真实标签

`03_review_clips.sql` 联查原始视频、状态、动作，提供前 2 秒/后 3 秒的初始复核窗口。
复核时应扩展到完整的一次抓取尝试，不把有多次重试的整集直接标为一次失败。
按查询结果中的 media_id 提取原 MP4：

```bash
python -m pipeline.export_media \
  --dataset-root oss://YOUR_BUCKET/robotics/droid100 \
  --media-id '0:observation.images.wrist_image_left' \
  --output work/review/episode_000000_wrist.mp4
```

该命令提取整段视频并校验 SHA256。原始视频位于 Lance 二进制列中，source_path 不是 OSS MP4 URL。

```bash
mkdir -p work/labels
cp doris/templates/grasp_review.csv work/labels/grasp_review.csv
cp doris/templates/episode_policy.csv work/labels/episode_policy.csv
```

模板只有表头，必须填写实际复核记录，项目不附带虚构成败标签。

| 字段 | 填写规则 |
|---|---|
| run_id / sample_id / episode_index | 使用本次 grasp_hits 的键，不改写编号 |
| reviewed / is_target_grasp / quality_ok | 布尔值 true/false；分别表示已复核、属于目标抓取、可用质量 |
| object_type / scene_type | 统一物体和场景词表，无法判断时填写 unknown |
| outcome | success / failure / unknown；不确定样本不进入成败比例分母 |
| failure_stage / failure_reason | 例如 contact / slip；成功时可填 not_applicable |
| clip_start_s / clip_end_s | 完整尝试的起止秒数，必须在视频范围内 |
| label_source | 复核来源，例如 human_review_v1 |
| scene_group | 按实际采集场景/会话分组；同一场景不得跨 train/validation/test |
| split_assignment | train / validation / test |
| used_for_training | 是否已经用于训练；必须来自真实训练登记 |

episode_policy 应覆盖整个待用数据集，包括留出的 episode，不能只登记搜索命中的训练候选。
源数据没有现成的场景划分和训练登记，需要由你的流程补充。未知登记在选样时排除。
CSV 列顺序保持与模板一致，文本使用无逗号、无换行的词表值，空值可用 `\N`。

填写后通过 [Stream Load](https://doris.apache.org/docs/4.x/data-operate/import/import-way/stream-load-manual/)
导入，`csv_with_names` 跳过表头；下面使用唯一 label 并交互输入密码：

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

检查返回 JSON 的 Status 和加载/过滤行数，不能把 HTTP 200 当成导入成功。
只有表头时先补充记录，不应继续做失败率分析。FE 重定向到 BE，客户端也需能访问 BE。
如果响应丢失，先按原 label 查询状态，避免盲目换 label 重试。

## 4. 分析分布并输出训练候选

```bash
mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/queries/droid100_demo_001/04_failure_distribution.sql

mysql -h "$DORIS_HOST" -P "$DORIS_QUERY_PORT" -u "$DORIS_USER" -p \
  < work/queries/droid100_demo_001/05_select_training_samples.sql
```

`04` 先统计复核覆盖率，再按物体/场景计算已知结果中的失败比例、按失败阶段/原因分布。
它描述的是这次检索并复核的样本群，不能外推为机器人线上失败率。

`05` 先检查跨 split 的场景组，再排除这些组、已训练样本、质量不合格样本及无效片段。
每个物体/场景/训练用途桶内按场景组去重，最多取 20 条。它输出可追溯的候选清单：
episode、sample、media、时间区间和训练用途，不执行训练或自动更新训练登记。
成功片段标为 `bc_positive`；失败片段标为 `failure_diagnosis_or_critic`，
不能将失败动作直接作为行为克隆的正确动作目标。

完整业务解释见 [案例说明](../docs/CASE_STUDY.zh-CN.md)，验证范围见 [验证记录](../docs/VALIDATION.zh-CN.md)。
