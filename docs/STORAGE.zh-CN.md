# 对象存储

[English](STORAGE.md) | [简体中文](STORAGE.zh-CN.md)

输出 URI 决定存储类型。AWS S3 和阿里云原生 OSS 使用相同的 Lance schema、embedding、
索引、checkpoint 和分析查询。请使用已有 bucket 下独立且非空的前缀；同一前缀只能有一个导出任务写入。

| 配置 | AWS S3 | 原生 OSS |
|---|---|---|
| 数据根路径 | `s3://bucket/robotics/droid100` | `oss://bucket/robotics/droid100` |
| Region | `AWS_REGION` 或 `AWS_DEFAULT_REGION`（必填） | `OSS_REGION` |
| Access key | `AWS_ACCESS_KEY_ID` | `OSS_ACCESS_KEY_ID` |
| Secret key | `AWS_SECRET_ACCESS_KEY` | `OSS_ACCESS_KEY_SECRET` |
| 临时 token | `AWS_SESSION_TOKEN` | `OSS_SECURITY_TOKEN` |
| Endpoint | AWS 自动生成；可选 `AWS_ENDPOINT` | 必填 `OSS_ENDPOINT` |
| Doris 建表模板 | `doris/sql/01_setup_s3.sql` | `doris/sql/01_setup.sql` |

凭证放在环境变量或被 Git 忽略的 `.env` 中，不要写入 Notebook 或受版本管理的 SQL。
修改 `.env` 后，分别执行 `set -a`、`source .env`、`set +a` 加载；Notebook 会自行加载 `.env`。
导出器需要前缀下列举、读写及分段上传权限；Doris 需要读取和列举权限。导出器不创建 bucket。

## AWS S3 导出与读取

参考 `.env.example` 填写 `AWS_REGION`、`AWS_ACCESS_KEY_ID` 和 `AWS_SECRET_ACCESS_KEY`。
普通 AWS S3 无需设置 `AWS_ENDPOINT`。临时凭证额外设置 `AWS_SESSION_TOKEN`，过期后刷新凭证再续传。
未显式设置密钥对时，由安装的 Lance SDK 解析其支持的 AWS 凭证；profile 或工作负载凭证需在项目外配置。
Doris FE/BE 的凭证需要单独配置。

```bash
python -m pipeline.export_droid \
  --output s3://YOUR_BUCKET/robotics/droid100 \
  --work-dir work/s3-full --frame-stride 15

python -m pipeline.export_droid \
  --output s3://YOUR_BUCKET/robotics/droid100 \
  --work-dir work/s3-full --index-only

python -m doris.prepare_query \
  --dataset-root s3://YOUR_BUCKET/robotics/droid100 \
  --sample-id '0:observation.images.wrist_image_left:0' \
  --run-id s3_demo_001 --output-dir work/queries/s3_demo_001

python -m pipeline.export_media \
  --dataset-root s3://YOUR_BUCKET/robotics/droid100 \
  --media-id '0:observation.images.wrist_image_left' \
  --output work/review/s3-episode0-wrist.mp4
```

中断后在原导出命令上添加 `--resume`，保持集数范围、采样步长和输出前缀一致。
三个工具均支持 `--s3-region`、`--s3-endpoint` 覆盖对应环境变量。
数据及 `_indices/` 索引文件都保存在相应的 S3 Lance dataset 内。

## Doris 与 Jupyter

复制 `doris/sql/01_setup_s3.sql` 到 `work/config/01_setup.sql`，填写 bucket、endpoint、region 和凭证。
用它替代 OSS 建表模板；后续 SQL 完全相同。设置 `use_path_style=false` 时，AWS 显式 endpoint
必须包含 bucket，例如 `https://my-bucket.s3.us-east-1.amazonaws.com`。
Python 可以使用 AWS 默认 endpoint，但 Doris 需要显式配置。
参见 [官方 Lance Catalog 示例](https://doris.apache.org/docs/4.x/lakehouse/catalogs/lance-catalog/)。

在 `.env` 中设置 `NOTEBOOK_DATASET_ROOT=s3://YOUR_BUCKET/robotics/droid100`，然后切换 live 模式。
首次创建 Catalog 时，`RUN_SETUP=True` 会选择 S3 模板，在内存中填写 endpoint、region 和凭证。
已有 Catalog 必须指向相同的前缀。
Notebook 自动创建 Catalog 支持显式密钥对。使用 STS 或工作负载 IAM 时，请按 Doris 构建的能力
单独配置 Catalog，并保持 `RUN_SETUP=False`；Notebook 不会把 Python SDK 的凭证或临时 token 复制到 Catalog 属性。

## S3 兼容服务

使用 MinIO 等服务时，将 `AWS_ENDPOINT` 设为服务地址，并使用 path style：

```dotenv
AWS_REGION=us-east-1
AWS_ENDPOINT=https://s3.example.com
AWS_VIRTUAL_HOSTED_STYLE_REQUEST=false
AWS_ALLOW_HTTP=false
```

使用该服务的 access/secret key。自定义 endpoint 默认采用 path style，地址中不包含 bucket。
若服务要求 virtual-hosted 请求，显式设置 `AWS_VIRTUAL_HOSTED_STYLE_REQUEST=true`，并在 endpoint
主机名中包含 bucket。开发服务只有 HTTP 时，需显式设置 `AWS_ALLOW_HTTP=true`；HTTPS 仍校验证书。
Notebook 会把请求方式映射到 Doris 的 `use_path_style`。手动编辑 SQL 时，path-style 示例应设置
`s3.endpoint`、`s3.region` 和 `use_path_style=true`。FE 和 BE 必须能自行访问该地址；
Python 本地可连通并不代表 Doris 已连通。

不同 S3 兼容服务的 API 和一致性行为可能不同。请先用独立前缀验证两集，再执行全量导出。
[验证记录](VALIDATION.zh-CN.md) 区分了本地 S3 协议测试与真实云服务、Doris 部署验证。

## 原生 OSS 与本地目录

现有 `oss://` 命令继续使用 `OSS_*` 和原来的 `01_setup.sql` 模板。
S3 与 OSS 配置按 URI 选择，不会把一种服务的密钥传给另一种服务。
本地目录输出不需要云凭证。SDK 配置细节参见 [Lance 对象存储文档](https://lance.org/guide/object_store/)。
