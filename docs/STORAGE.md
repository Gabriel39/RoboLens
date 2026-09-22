# Object storage

[English](STORAGE.md) | [简体中文](STORAGE.zh-CN.md)

The output URI selects the storage provider. The same Lance schemas, embeddings, indexes,
checkpoints, and analysis queries work with AWS S3 and native Alibaba Cloud OSS.
Use an existing bucket and a nonempty, dedicated prefix. Only one exporter may write that prefix.

| Setting | AWS S3 | Native OSS |
|---|---|---|
| Dataset root | `s3://bucket/robotics/droid100` | `oss://bucket/robotics/droid100` |
| Region | `AWS_REGION` or `AWS_DEFAULT_REGION` (required) | `OSS_REGION` |
| Access key | `AWS_ACCESS_KEY_ID` | `OSS_ACCESS_KEY_ID` |
| Secret key | `AWS_SECRET_ACCESS_KEY` | `OSS_ACCESS_KEY_SECRET` |
| Temporary token | `AWS_SESSION_TOKEN` | `OSS_SECURITY_TOKEN` |
| Endpoint | Automatic for AWS; optional `AWS_ENDPOINT` | Required `OSS_ENDPOINT` |
| Doris setup | `doris/sql/01_setup_s3.sql` | `doris/sql/01_setup.sql` |

Credentials belong in your environment or ignored `.env`, not in notebooks or tracked SQL.
After editing `.env`, load it with `set -a`, `source .env`, and `set +a` on separate shell lines.
The notebook loads `.env` itself. The exporter needs list/read/write access under its prefix,
including multipart uploads; Doris needs read/list access. Buckets are not created by the exporter.

## AWS S3 export and reads

Configure `AWS_REGION`, `AWS_ACCESS_KEY_ID`, and `AWS_SECRET_ACCESS_KEY` using `.env.example`.
Leave `AWS_ENDPOINT` unset for standard AWS S3. The exporter also accepts temporary credentials
through `AWS_SESSION_TOKEN`. Refresh expired credentials before resuming. Without an explicit key
pair, credential resolution is delegated to the installed Lance SDK; configure its supported AWS
profile or workload credentials outside the project. FE/BE credentials are configured separately.

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

Add `--resume` to the original export command after interruption. Preserve the episode range,
stride, and output prefix. `--s3-region` and `--s3-endpoint` override the corresponding environment
settings in all three tools. Data and `_indices/` files stay inside the same S3 Lance datasets.

## Doris and Jupyter

Copy `doris/sql/01_setup_s3.sql` to `work/config/01_setup.sql` and replace the bucket, endpoint,
region, and credentials. Run this instead of the OSS setup template. Subsequent SQL is identical.
With `use_path_style=false`, the explicit AWS endpoint must include the bucket, for example
`https://my-bucket.s3.us-east-1.amazonaws.com`. Doris requires an endpoint even when the Python
exporter can use the AWS default. See the [official Lance Catalog examples](https://doris.apache.org/docs/4.x/lakehouse/catalogs/lance-catalog/).

Set `NOTEBOOK_DATASET_ROOT=s3://YOUR_BUCKET/robotics/droid100` in `.env`, then select live mode.
For a new catalog, `RUN_SETUP=True` selects the S3 template and fills its endpoint, region, and
credentials in memory. Existing catalogs must already point at this same prefix.
Automatic notebook catalog creation supports explicit key pairs. With STS or workload IAM,
configure your Doris catalog independently for your build and keep `RUN_SETUP=False`; the
notebook does not copy Python SDK credentials or temporary tokens into catalog properties.

## S3-compatible services

For a service such as MinIO, set `AWS_ENDPOINT` to its origin and use path-style requests:

```dotenv
AWS_REGION=us-east-1
AWS_ENDPOINT=https://s3.example.com
AWS_VIRTUAL_HOSTED_STYLE_REQUEST=false
AWS_ALLOW_HTTP=false
```

Use that service's access/secret keys. With a custom endpoint, path style is the default, so the
endpoint must omit the bucket. If the service requires virtual-hosted requests, explicitly set
`AWS_VIRTUAL_HOSTED_STYLE_REQUEST=true` and include the bucket in the endpoint hostname.
For an HTTP-only development service, set `AWS_ALLOW_HTTP=true` explicitly; certificate verification
remains enabled for HTTPS. The notebook maps request style to Doris `use_path_style`.
When editing SQL manually, set `s3.endpoint`, `s3.region`, and `use_path_style=true` for the
path-style example. FE and BE must reach the endpoint themselves; a local Python connection does
not establish Doris connectivity.

S3-compatible providers vary in API and consistency behavior. Validate a separate two-episode
prefix on your service before a full run. The [validation record](VALIDATION.md) distinguishes
local S3 protocol testing from a real cloud or Doris deployment.

## Native OSS and local files

Existing `oss://` commands keep using `OSS_*` and the original `01_setup.sql` template.
The S3 and OSS settings are selected by URI, so one provider's keys are not passed to the other.
A local output directory uses neither provider and needs no cloud credentials.
See [Lance object-store configuration](https://lance.org/guide/object_store/) for SDK details.
