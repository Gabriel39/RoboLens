# 数据导入、加工与索引

[English](README.md) | [简体中文](README.zh-CN.md)

从项目根目录运行 `python -m pipeline.export_droid`。入口、配置和两集/全量运行命令见
[根 README](../README.zh-CN.md)。本目录负责源数据到 OSS Lance，不连接 Doris。

## 处理步骤

1. 固定读取 `lerobot/droid_100` 的 v2.1 commit，读取全部 `meta/` 文件。
2. 按 episode 下载 Parquet 和三路 AV1 MP4，验证帧数、episode/global index、时间对齐。
3. 保留源数值类型和全部原始列；字段名中的点改为下划线，并追加任务文字和源路径。
4. 解码图片，按采样率生成向量；保留原 MP4 字节、SHA256 和视频聚合向量。
5. 本地 Arrow IPC 暂存一批数据，然后提交各 Lance 表，最后提交批次 checkpoint。
6. 校验全量行数，在非空向量表构建 cosine 索引，校验覆盖行数后发布索引就绪状态。

## 模型与向量

| 内容 | 模型 | 向量 | 说明 |
|---|---|---|---|
| 视频帧 | `google/siglip2-base-patch16-224` | 768 维 float32，L2 归一化 | 默认逐帧；模型 revision 固定 |
| 视频整体 | 同一视频的采样帧向量平均后再归一化 | 768 维 | 不表达动作先后顺序 |
| 音轨 | `laion/clap-htsat-unfused` | 512 维，L2 归一化 | 48 kHz 单声道，每 10 秒一个窗口 |

模型下载后本地推理，不调用在线 embedding API。原 DROID-100 无音轨，因此不会下载/加载
CLAP，音频表为 0 行。脚本仍检查实际音轨，遇到音频才进入该流程。
state/action 是原始机器人记录，保留 7 维数值，不经过 embedding 模型。

## 常用参数

| 参数 | 默认 | 含义 |
|---|---|---|
| `--output` | 必填 | `oss://bucket/prefix` 或本地目录 |
| `--work-dir` | `./work` | 模型缓存、源元数据和批次临时文件 |
| `--source-root` | 无 | 已下载的 DROID-100 v2.1 根目录；包含 meta/data/videos |
| `--max-episodes` | 无限制 | 小样本数量；与 start-episode 共同定义范围 |
| `--start-episode` | 0 | 起始 episode |
| `--frame-stride` | 1 | 仅控制向量采样；15 对应约每秒一帧 |
| `--embedding-batch-size` | 32 | 图像推理批次；显存不足时降低 |
| `--episodes-per-commit` | 16 | 暂存/提交/失败重算的批次大小 |
| `--device` | auto | 自动 CUDA/CPU，也可显式传 cpu/cuda/cuda:0 |
| `--timestamp-tolerance` | 0.04 秒 | 画面与状态的时间误差上限 |
| `--resume` | 关闭 | 恢复当前导出并继续索引阶段 |
| `--index-only` | 关闭 | 仅为完整导出补建索引，不读取 HF 源或模型 |
| `--skip-indexes` | 关闭 | 只导出数据，以后补索引 |
| `--index-type` | IVF_PQ | 可改 IVF_FLAT；少于 256 行自动使用 IVF_FLAT |
| `--index-target-partition-size` | 8192 | 每个 IVF 分区的目标向量数 |
| `--index-num-sub-vectors` | 64 | PQ 子向量数，须同时整除 768/512 |
| `--index-shuffle-batches` | 32 | shuffle 工作批次数，不是总内存上限 |

已有本地源时仍可能下载模型；源文件在导出和恢复期间应保持不变。
`--revision` 可用于显式选择其他 v2.1 提交，但不支持 v3 的共享文件布局。
默认使用固定源 commit，不建议改为移动分支。

## 索引及恢复

索引列为 `frame_embeddings.embedding`、`media.video_embedding`、非空的
`audio_embeddings.embedding`；它们直接存放于 OSS Lance dataset。

IVF_PQ 默认 64 个子向量、SDK 默认 8-bit 码本。小于 256 行无法训练该 PQ 码本，
因此自动改用 IVF_FLAT。索引参数与名称摘要关联，开始后需沿用原参数；不会静默覆盖
另一套索引。索引训练默认 CPU，不由图像推理的 `--device` 控制。

同一输出前缀只能有一个写入进程。工作目录文件锁只能防止同机同目录重复启动，
不是分布式锁。数据阶段中断会回退到上个完整 checkpoint，避免多表半批或重复数据。
数据完成后，只允许索引元数据变化；`--resume` 保留已经成功的索引。
单个未提交索引需要重建；如果提交已成功但客户端断线，重跑会发现并复用索引。

`export_checkpoints.complete` 表示数据完成；`export_index_status.complete` 才表示所有
非空向量表已建立并验证索引。空音频表记录为 empty。索引阶段失败时不会发布成功状态。
外部修改数据或 compaction 会改变 fragments，恢复将拒绝继续；完成整个流程前保留
checkpoint 引用的旧版本。脚本不做自动压缩或旧版本清理。

模型缓存保留；正常退出时清理批次临时文件。断电留下的 `work/batch-*` 目录可在确认
进程停止后手动删除。为向量、索引、模型和暂存空间预留磁盘及内存，GPU 不是存储导出的必要条件。

## 导出原始视频供复核

```bash
python -m pipeline.export_media \
  --dataset-root oss://YOUR_BUCKET/robotics/droid100 \
  --media-id '0:observation.images.wrist_image_left' \
  --output work/review/episode0-wrist.mp4
```

工具按 media_id 读取一个 MP4，验证 SHA256 后写本地文件，拒绝覆盖现有文件。
视频时间可结合 `frame_embeddings.video_timestamp` 与源 `timestamp` 对齐；这个工具不裁剪或重新编码。
