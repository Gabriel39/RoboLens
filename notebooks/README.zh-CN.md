# Jupyter 全流程演示

[English](README.md) | [简体中文](README.zh-CN.md)

入口：[droid100_end_to_end.ipynb](droid100_end_to_end.ipynb)。
直接展示：[droid100_preview.html](droid100_preview.html)，用浏览器打开即可。

Notebook 包含 **1 张总览图 + 8 张步骤示意图**，每张图说明输入、处理和输出：

1. 固定版 DROID-100 源文件和数据关联。
2. 视频解码、采样、SigLIP2 embedding。
3. 写入 S3 / OSS Lance、checkpoint 和向量索引。
4. Doris Catalog 与业务分析内表。
5. `vector_search()` 与 episode 去重。
6. 视频回放、状态/动作关联、人工复核。
7. 复核覆盖率、失败分布与统计分母。
8. 训练登记、场景去重、训练候选。

示意图为本地 SVG，
会随 Notebook 输出嵌入 HTML，不依赖在线画图服务。

S3 配置见 [存储指南](../docs/STORAGE.zh-CN.md)。下文 `oss://` 示例可替换为对应的 `s3://` 路径；Doris 使用 `01_setup_s3.sql` 模板，Notebook 根据 `NOTEBOOK_DATASET_ROOT` 自动选择。

## 安装和启动

在项目根目录执行：

```bash
source .venv/bin/activate
python -m pip install -r requirements-notebook.txt
python -m ipykernel install --sys-prefix --name droid100 --display-name "Python (DROID-100)"
python -m jupyterlab notebooks/droid100_end_to_end.ipynb
```

在 JupyterLab 选择 `Python (DROID-100)` 内核。尚未安装项目时，先按根 README 创建虚拟环境。
Notebook 可以从项目根目录或 notebooks 目录启动；不要只拷贝 ipynb，需保留项目代码、SQL 和 assets。

## 两种运行方式

### preview：直接讲解整体流程

默认配置为 `MODE="preview"`。执行 Run All 会展示流程图、固定源规模、SQL 模板和每步解释。
不会下载数据/模型或连接数据库，不会生成假检索结果、假失败率。预览 HTML 已执行此模式。

只看预览 HTML 不需要安装任何 Python 依赖。

### live：真实执行导入与查询

1. 在项目根目录配置 `.env` 中的 S3 / OSS/Doris 信息，可设 `NOTEBOOK_DATASET_ROOT` 指向输出前缀。
   密码可以通过 Notebook 的 getpass 输入框输入，不必写入 `.env`。
2. 配置单元改为 `MODE="live"`；首次导出设 `RUN_EXPORT=True`，首次建 Catalog/表设 `RUN_SETUP=True`。
   已有完整导出或 Catalog 时相应设为 False。首次导出前确认依赖已安装、bucket 已存在。
3. 顺序执行到第 06 步，查看真实检索结果、视频和状态/动作。导出单元会一次执行第 01–03 步；
   每个阶段的内部处理由前面的示意图解释。现有 CLI 负责实际下载、推理、写表和建索引。
4. **停在标签导入之前**，填写 `work/notebook/labels` 下两份 CSV。字段规则见
   [Doris README](../doris/README.zh-CN.md)。episode_policy 需覆盖本次导出的所有 episode。
5. 新建单元执行 `IMPORT_LABELS = True`，重跑标签导入单元及第 07–08 步，得到真实统计和候选。
   不要重跑配置单元，否则会生成新的 run_id。不要重复运行首次建表单元。
6. SQL 结果以 DataFrame 和图表展示；检索、覆盖率、分布与候选 CSV 会保存到
   `work/notebook/queries/<run_id>/report/`。

默认导出 100 集、frame-stride=15，约 6,591 条帧向量；已有逐帧导出也能直接查询。
用两集试跑时换独立的 S3 / OSS 前缀，试跑期间 Catalog 也需指向这份导出，不能只改变 Notebook 参数。
切回全量时应重新正确配置 Catalog。恢复导出沿用同一配置并设 `RESUME_EXPORT=True`。
没有候选、没有标签或没有训练登记时，Notebook 明确显示空结果，不编造图表。

最后一个单元会关闭数据库连接。若提前 Run All 后要补标签，先把 RUN_SETUP 改为 False，
仅重跑第 04 步的连接单元，再继续标签导入及第 07–08 步，保持原 run_id。
执行 SQL 是逐条提交，不是整个 Notebook 的事务；失败后从对应步骤恢复。

若源 AV1 MP4 在浏览器中无法播放，Notebook 仍展示解码后的关键帧，完整 MP4 保留在
`work/notebook/media` 中，可用支持 AV1 的播放器打开。

## 作为展示页面

| 形式 | 适合用途 | 能否重新执行 |
|---|---|---|
| JupyterLab Notebook | 现场演示、修改参数、查看真实结果 | 能，需要内核和数据连接 |
| 导出的 HTML | 汇报、浏览器展示、发送执行快照 | 不能执行 Python/SQL |

在 Notebook 保存实际执行输出后导出：

```bash
mkdir -p work/presentation
jupyter nbconvert notebooks/droid100_end_to_end.ipynb \
  --to html --no-input --embed-images --output-dir work/presentation
```

此命令仅转换已保存的内容，不重跑昂贵的数据处理。代码会隐藏，示意图、结果表、图表、
已嵌入的视频仍保留。视频会增大文件体积。分享前检查输出内容；隐藏代码不会自动移除敏感结果。
[官方 nbconvert 文档](https://nbconvert.readthedocs.io/en/latest/usage.html)介绍 HTML 和幻灯片导出。

分享 HTML 时，把两个语言版本放在同一目录，顶部链接即可切换。文件分别为
`droid100_preview.html` 与 `droid100_preview.en.html`。保存两份 Notebook 后可统一刷新：

```bash
python -m notebooks.export_presentations
```

工具只转换已保存输出，不运行单元；若改了 live 流程，请先执行并保存对应 Notebook。
GitHub 源码页面不会直接运行 HTML；可下载仓库后在浏览器打开展示页。

## 验证边界

已在本地完整执行 preview 模式并导出 HTML；SQL/标签辅助函数另有离线测试。
安装 Notebook 和开发依赖后，运行 `python -m pytest -q` 可执行全部 34 项测试。
live 模式复用已有导出和查询代码，但尚未连接用户的 S3 / OSS/Doris 环境，不能把附带页面当作真实业务报告。
源视频和模型不包含在项目压缩包内；详见 [验证记录](../docs/VALIDATION.zh-CN.md)。
