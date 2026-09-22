# RoboLens

[English](README.md) | [简体中文](README.zh-CN.md)

**Explore robot behavior through data.**

DROID-100 → S3 / OSS Lance → Doris Vector Search

RoboLens 使用 Python 下载和加工 DROID-100，生成 embedding、写入 S3 / OSS 上的 Lance
并构建向量索引；Doris 通过 `vector_search()` 搜索相似片段，再分析抓取失败分布和筛选训练候选。

[Jupyter 全流程演示](notebooks/README.zh-CN.md)：包含 1 张总览图和 8 张步骤示意图，
可逐步执行导入、Vector Search、视频复核和失败分析，也可导出隐藏代码的 HTML 展示页。
无需安装即可先打开 [流程预览页](notebooks/droid100_preview.html)。预览页不包含虚构业务结果。

## 数据版本与规模

数据源固定为 [`lerobot/droid_100`](https://huggingface.co/datasets/lerobot/droid_100)，
使用官方 **v2.1 标签**对应的提交 `78f887947f976d85dd04594bcbd0b05c29893349`。
该仓库的 `main` 已变为 v3.0；本项目有意固定 v2.1，每个 episode 单独存放 Parquet/MP4，
不将 `main` 的共享视频布局误当成每集一个文件。

| 内容 | 固定版本中的数量 |
|---|---:|
| Episode | 100 |
| 状态/动作时间步 | 32,212 |
| 任务文字条目 | 47 |
| MP4 | 300，三路相机 |
| 帧率 | 15 fps |
| observation.state / action | 都是 7 维 float32 |
| 逐帧图像向量 | 96,636 条，768 维 |
| stride=15 的图像向量 | 6,591 条，768 维 |
| 视频聚合向量 | 300 条，768 维 |
| 音频向量 | 源视频无音轨，正常为 0 条 |

除了 state/action，也原样保留 `next.reward`、`next.done`；输出时改名为
`next_reward`、`next_done`。它们不自动等同于一次抓取的成功/失败标签。
字段定义见 [docs/SCHEMA.md](docs/SCHEMA.zh-CN.md)。

## 项目目录

```text
RoboLens/
  README.md
  requirements.txt
  requirements-dev.txt
  requirements-notebook.txt
  .env.example
  pipeline/
    README.md
    export_droid.py
    export_media.py
  doris/
    README.md
    prepare_query.py
    sql/
      01_setup.sql
      01_setup_s3.sql
      02_vector_search.sql
      03_review_clips.sql
      04_failure_distribution.sql
      05_select_training_samples.sql
    templates/
      grasp_review.csv
      episode_policy.csv
  docs/
    SCHEMA.md
    CASE_STUDY.md
    VALIDATION.md
  notebooks/
    README.md
    droid100_end_to_end.ipynb
    droid100_preview.html
    demo_support.py
    assets/
  tests/
    test_export.py
```

## 1. 安装

克隆仓库后在项目根目录执行；建议 Linux、Python 3.11/3.12。GPU 用于加速 embedding，CPU 也能运行。
Lance 索引默认在 CPU 上构建。

```bash
git clone https://github.com/Gabriel39/RoboLens.git
cd RoboLens
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

按机器的 CUDA/CPU 环境安装相应 PyTorch wheel。编辑 `.env` 填入 S3 / OSS 凭证和 endpoint，
再加载变量：

```bash
set -a
source .env
set +a
```

密钥只从环境读取，不写入导出 manifest。脚本不会创建 S3 / OSS bucket；使用已存在的 bucket
和有读、列举、写权限的凭证。临时 STS token 到期后更新环境变量并恢复任务。

## AWS S3

使用 AWS S3 时，在 `.env` 中填写 `AWS_REGION`、`AWS_ACCESS_KEY_ID` 和
`AWS_SECRET_ACCESS_KEY`（临时凭证还需 `AWS_SESSION_TOKEN`），输出路径改为 `s3://`：

```bash
python -m pipeline.export_droid \
  --output s3://YOUR_BUCKET/robotics/droid100_smoke \
  --work-dir work/s3-smoke --max-episodes 2 --frame-stride 15
```

所有导出、续传、建索引、查询向量和视频提取命令均支持 S3。Doris 使用
`doris/sql/01_setup_s3.sql`；Notebook 设置 `NOTEBOOK_DATASET_ROOT=s3://YOUR_BUCKET/robotics/droid100`。
详见 [S3、S3 兼容服务与 OSS 配置](docs/STORAGE.zh-CN.md)。下文保留 OSS 命令示例；
使用 S3 时将路径换成对应的 `s3://` 前缀，并使用 AWS 配置。

## 2. 先验证两集，再导出全部 100 集

小样本和全量必须使用不同的输出前缀，因为 episode 范围属于导出配置的一部分。

```bash
python -m pipeline.export_droid \
  --output oss://YOUR_BUCKET/robotics/droid100_smoke \
  --work-dir work/smoke \
  --max-episodes 2 \
  --frame-stride 15
```

全量保留所有帧的 embedding：

```bash
python -m pipeline.export_droid \
  --output oss://YOUR_BUCKET/robotics/droid100 \
  --work-dir work/full \
  --device auto \
  --frame-stride 1
```

不传 `--max-episodes` 就是全部 100 集。需要减少 embedding 计算量时用 `--frame-stride 15`；
原始视频和状态/动作仍完整保存。显存不足时降低 `--embedding-batch-size`。
离线验证可将 `--output` 改为 `work/local-output`，不需要 S3 / OSS 凭证。

输出包含六个业务 dataset 和三个运行记录 dataset：

```text
oss://YOUR_BUCKET/robotics/droid100/
  frames.lance/
  episodes.lance/
  media.lance/
  frame_embeddings.lance/
  audio_embeddings.lance/
  metadata.lance/
  export_manifest.lance/
  export_checkpoints.lance/
  export_index_status.lance/
```

原始视频作为 `media.video_bytes` 写入 Lance，索引保存在相应 dataset 的 `_indices/` 中。
它们不是独立 S3 / OSS MP4 地址，也不需要再导入 Doris 内表才能进行 Vector Search。

## 3. 恢复与补建索引

导出中断：使用原来的数据范围、采样率、输出前缀，加上 `--resume`。

```bash
python -m pipeline.export_droid \
  --output oss://YOUR_BUCKET/robotics/droid100 \
  --work-dir work/full \
  --frame-stride 1 \
  --resume
```

已有完整 DROID-100 导出，仅补建或恢复索引：

```bash
python -m pipeline.export_droid \
  --output oss://YOUR_BUCKET/robotics/droid100 \
  --work-dir work/full \
  --index-only
```

默认构建 cosine IVF_PQ 索引，小于 256 行的向量表自动用 IVF_FLAT；空音频表跳过。
已提交的索引会复用，索引恢复不重新下载数据或计算 embedding。
**不要复用此前 `cadene/droid` 的输出前缀或 source-root**：源数据、状态维度及配置均不同。
完整参数、模型和断点规则见 [pipeline/README.md](pipeline/README.zh-CN.md)。

## 4. Doris 查询分析

按 [doris/README.md](doris/README.zh-CN.md) 的顺序执行：

1. 配置 `01_setup_s3.sql`（S3）或 `01_setup.sql`（OSS）中的 bucket/凭证，建立 Lance Catalog 和分析表。
2. 从真实参考帧生成 SQL，工具自动填入 768 维查询向量。
3. 执行 Vector Search，获取片段复核清单。
4. 导入实际的人工复核标签与训练集登记，执行失败分布和选样 SQL。

生成一个连通性演示查询：

```bash
python -m doris.prepare_query \
  --dataset-root oss://YOUR_BUCKET/robotics/droid100 \
  --sample-id '0:observation.images.wrist_image_left:0' \
  --run-id droid100_demo_001 \
  --output-dir work/queries/droid100_demo_001
```

这张参考帧确实存在，但只是运行演示，**不声明它代表抓取失败**；真实分析应换成人工定位的
失败关键帧。也可对新采集的本地图片使用 `--image`，工具调用同版 SigLIP2。
工具只生成 SQL，不连接 Doris，也不会自动生成成败标签。

需要具备 Lance Catalog 和 `vector_search()` 的 Doris 构建；当前官方公开 4.x 文档标注
该 Catalog 从 4.2 起支持，实际环境请核对功能。案例不会手写距离函数做全表相似度排序。

## 5. 测试与验证范围

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

测试覆盖真实媒体编解码、数值/字节保真、索引和恢复、查询 SQL 生成。
模型使用明确的测试替身；索引使用真实 Lance SDK。另验证过真实 DROID-100 第 0 集，
详情和未验证的环境边界见 [docs/VALIDATION.md](docs/VALIDATION.zh-CN.md)。

业务案例见 [docs/CASE_STUDY.md](docs/CASE_STUDY.zh-CN.md)。代码未连接用户的 S3 / OSS/Doris 环境执行。

## 官方资料

- [固定数据版本](https://huggingface.co/datasets/lerobot/droid_100/tree/78f887947f976d85dd04594bcbd0b05c29893349)
- [Doris Lance Catalog / Vector Search](https://doris.apache.org/docs/4.x/lakehouse/catalogs/lance-catalog/)
- [Lance 对象存储配置](https://lance.org/guide/object_store/)
- [SigLIP2](https://huggingface.co/google/siglip2-base-patch16-224)
- [CLAP](https://huggingface.co/laion/clap-htsat-unfused)
