# 验证记录

[English](VALIDATION.md) | [简体中文](VALIDATION.zh-CN.md)

验证日期：2026-09-22。测试环境：Linux、Python 3.12、pylance 11.0.0、PyArrow 22.0.0、PyAV 16.1.0。

## 自动化测试

在项目根目录运行 `python -m pytest -q`，结果 **24 passed**。

覆盖范围：原始 state/action/reward/done 数值和类型保留、视频字节与 SHA256、采样和时间对齐、
音频分块、实际 Lance 读写和向量索引、索引参数与覆盖率、部分写入恢复、完整导出恢复时保留索引、
拒绝外部数据修改、不兼容源版本/维度、真实向量读取和 SQL 模板替换、输入校验、媒体提取和防覆盖，
以及媒体数据完整性检查。

测试使用真实 PyAV 和 Lance SDK，embedding 使用明确的测试替身，不能据此验证语义检索质量。
生产入口没有伪造 embedding 的运行模式。SDK 的 list_indices 接口产生弃用警告，固定版本内功能正常。

## 真实 DROID-100 文件验证

从固定 commit `78f887947f976d85dd04594bcbd0b05c29893349` 下载了四个 meta 文件，
以及第 0 集真实 Parquet 和三路 AV1 MP4。元数据确认全量 100 集、32,212 时间步、47 个任务，
15 fps、三路相机、state/action 各 7 维。第 0 集任务是 `Put the marker in the pot`。

本地实际导出第 0 集，以 frame-stride=15 采样，结果：

| Dataset | 本地验证行数 |
|---|---:|
| frames | 166 |
| episodes | 1 |
| media | 3 |
| frame_embeddings | 36 |
| audio_embeddings | 0 |
| metadata | 4 |

对照源 Parquet 校验所有原始列和类型；对照源 MP4 校验完整二进制内容。
成功解码真实 AV1 视频，在帧向量和视频向量表上构建并验证 cosine IVF_FLAT 索引；
测试数据较少，按脚本规则从默认 IVF_PQ 自动切换。空音频表跳过索引。

此外，使用第 0 集第 0 帧实际写出的向量，通过命令行生成了 `02`–`05` 查询文件，
并通过媒体提取命令导出了 SHA256 校验通过的 wrist MP4。
这次真实文件验证仍使用测试模型替身，生成的测试向量不能用于业务检索。

## 尚未验证的范围

- 未下载并导出全部 100 集；全量规模取自固定源元数据，向量条数按各集长度和采样规则计算。
- 未在本机加载完整 SigLIP2/CLAP 权重进行推理，未评估 GPU 吞吐和语义召回率。
- 未连接用户 OSS，未验证实际凭证、网络、对象存储吞吐。
- 未连接 Doris 执行 SQL，SQL 依据官方 Lance Catalog/Vector Search 和 Stream Load 文档编写。
- 未生成或假设真实失败标签，未产出实际失败比例和训练收益结论。

实际运行时，先按根 README 使用独立输出前缀做两集验证，再进行全量导出和 Doris 查询。
打包文件不包含测试替身生成的 Lance 数据、模型缓存、真实源视频、密钥或虚构复核标签。

## Jupyter 演示补充验证

Notebook 包含 34 个单元，1 张总览图和 8 张分步骤 SVG 示意图。
使用独立 Python 内核完整执行 preview 模式，全部代码单元无错误，随后导出隐藏输入的 HTML。
浏览器确认 9 张嵌入图均成功加载，并检查了页面及图内文字排版。

Notebook 辅助测试覆盖 SQL 字符串内分号的正确切分、参数化标签写入、错误 run/episode 标签、
非法布尔值/时间/结果值的写前拒绝，以及缺失评估集登记时的拒绝。
安装 Notebook 依赖后，完整测试套件为 **34 passed**；仅安装基础开发依赖时可跳过可选 Notebook 测试。
已生成的 HTML 是流程讲解预览，未连接 OSS/Doris，也不包含推测的检索命中或失败统计。
