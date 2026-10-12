# 统一配置

项目根目录 `.env` 是 Notebook 和 Python CLI 的统一配置入口。首次配置可复制 `.env.example`。
真实 `.env` 已被 Git 忽略；密码和对象存储密钥只填在这里。不要复制整个配置文件到 Notebook 输出中。

```dotenv
DORIS_HOST=127.0.0.1
DORIS_QUERY_PORT=9030
DORIS_HTTP_PORT=8030
DORIS_USER=root
# 取消下一行注释并填写实际密码；未设置时 Notebook 会提示输入。
# DORIS_PASSWORD='实际密码'
NOTEBOOK_DATASET_ROOT=oss://YOUR_BUCKET/robotics/droid100

DORIS_CATALOG=droid100
DORIS_DATABASE=default
DORIS_ANALYSIS_DATABASE=droid100_analysis
DORIS_TABLE_GRASP_HITS=grasp_hits
DORIS_TABLE_GRASP_REVIEW=grasp_review
DORIS_TABLE_EPISODE_POLICY=episode_policy
```

`DORIS_QUERY_PORT` 用于 PyMySQL 查询连接；`DORIS_HTTP_PORT` 是 Web/HTTP 入口，不参与 SQL 连接。
`DORIS_DATABASE=default` 对应 Lance filesystem Catalog 暴露的数据库，应与实际 Catalog 一致。
OSS 或 S3 的 endpoint、region、访问密钥仍填写在同一个 `.env` 中，字段见 `.env.example`。
密码包含 `#` 或空格时使用引号；读取配置时不展开 `${...}`，密码中的美元符号会按原文保留。
显式的 `DORIS_PASSWORD=` 代表空密码；不配置该项则保留交互输入。

## 表名

- `DORIS_TABLE_*`：Doris 内表。建表、检索结果写入、标签导入、统计和候选查询统一使用这些值。
- `LANCE_TABLE_*`：物理 Lance 目录名，不带 `.lance` 后缀。导出、索引、Notebook 读取、参考帧检索和视频提取统一使用这些值。
- Catalog、数据库和表名仅允许字母、数字和下划线，不能以数字开头；SQL 标识符会加反引号。

默认数据表是 `frames`、`episodes`、`media`、`frame_embeddings`、`audio_embeddings`、`metadata`；
运行记录表是 `export_manifest`、`export_checkpoints`、`export_index_status`。
所有默认表名均列在 `.env.example`，无需到 Python 或 SQL 中逐处修改。

修改配置不会重命名已存在的 Catalog、表或 Lance 目录。已有数据继续使用原名称；新名称应配合独立的导出前缀和正确的 Catalog 初始化。
导出 manifest 会记录非默认的 Lance 表名，恢复导出和索引时会检查一致性。

## 加载与重载

`.env` 中的值优先于同名环境变量；未写入文件的配置仍可来自进程环境。Python CLI 自动从项目根目录读取 `.env`。
Notebook 配置单元使用 `CONFIG = load_config(PROJECT / ".env")`；修改文件后须重新运行配置单元，并重新建立 Doris 连接。
配置单元会重新生成 `RUN_ID`，因此不要在同一次人工复核中途重跑它。
首次更新代码后，应保存当前 Notebook、重新从磁盘加载，并重启内核，以确保导入新版本的辅助函数。

`MODE`、`RUN_EXPORT`、`RUN_SETUP` 等执行开关仍保留在 Notebook 中。已有完整导出和 Catalog 时，
将 `RUN_EXPORT=False`、`RUN_SETUP=False`，避免重做初始化。

## SQL 模板

`doris/sql/*.sql` 使用 `{{CATALOG}}`、`{{SOURCE_*}}`、`{{ANALYSIS_*}}` 标记，不是可直接执行的最终 SQL。
Notebook 的初始化函数会同时替换名称和存储连接参数。
`python -m doris.prepare_query ...` 会读取同一个 `.env`，输出可执行的检索及分析 SQL；这些查询文件不包含连接密码。
对已有 Catalog 运行查询时，无需重复执行初始化。

## 错误诊断

SQL 执行失败时，Notebook 会显示失败语句序号、SQL、原始异常类型、Doris 错误码及完整 traceback（包括异常链），并停止执行后续语句。此前成功的语句可能已提交。配置和数据库连接中的密码、访问密钥及 token 会替换为 `[REDACTED]`；不再用通用的“初始化失败”提示隐藏数据库错误。导出失败时显示进程退出码，完整子进程日志仍在单元格输出中。
