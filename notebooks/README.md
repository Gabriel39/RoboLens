# Jupyter end-to-end walkthrough

[English](README.md) | [简体中文](README.zh-CN.md)

Open [droid100_end_to_end.en.ipynb](droid100_end_to_end.en.ipynb), or view the
[English HTML preview](droid100_preview.en.html) directly in a browser.

The notebook contains **one overview and eight step diagrams**, each explaining inputs, processing, and outputs:

1. Pinned DROID-100 source files and join keys.
2. Video decoding, sampling, and SigLIP2 embeddings.
3. S3 / OSS Lance writes, checkpoints, and vector indexes.
4. Doris Catalog and internal analysis tables.
5. `vector_search()` and episode deduplication.
6. Video playback, state/action joins, and human review.
7. Review coverage, failure distributions, and statistical denominators.
8. Training history, scene deduplication, and training candidates.

Local SVG diagrams are embedded in the exported HTML and do not depend on an online diagram service.

See the [storage guide](../docs/STORAGE.md) for S3. Replace the `oss://` examples below with your `s3://` paths; use `01_setup_s3.sql` for Doris. The notebook selects the provider from `NOTEBOOK_DATASET_ROOT`.

## Install and start

From the project root:

```bash
source .venv/bin/activate
python -m pip install -r requirements-notebook.txt
python -m ipykernel install --sys-prefix --name droid100 --display-name "Python (DROID-100)"
python -m jupyterlab notebooks/droid100_end_to_end.en.ipynb
```

Select the `Python (DROID-100)` kernel in JupyterLab. If the project is not installed yet, create the
virtual environment following the root README. Start Jupyter from the project root or notebooks
directory. Do not copy only the ipynb: retain the project code, SQL, and assets.

## Two execution modes

### preview: present the workflow offline

The default is `MODE="preview"`. Run All displays diagrams, the pinned source size, SQL templates,
and step explanations. It does not download data/models, connect to a database, or invent search
results or failure rates. The included HTML was executed in this mode. Viewing it requires no
Python installation.

### live: run actual ingestion and queries

1. Configure S3 / OSS/Doris in the root `.env`; optionally set `NOTEBOOK_DATASET_ROOT`. Enter the database password through getpass if you prefer not to store it in `.env`.
2. Change `MODE` to `"live"`. Set `RUN_EXPORT=True` for a first export and `RUN_SETUP=True` for initial catalog/table creation. Keep the respective flag False when an export or catalog already exists. Install dependencies and create the bucket before exporting.
3. Execute through step 06 to inspect real retrieval results, videos, and state/action rows. One export cell runs steps 01–03 together; preceding diagrams explain each phase. The existing CLI performs downloads, inference, writes, and indexing.
4. **Pause before importing labels** and fill the two CSVs under `work/notebook/labels`. See the [Doris guide](../doris/README.md) for fields. episode_policy must cover every exported episode.
5. Run `IMPORT_LABELS = True` in a new cell, then rerun label import and steps 07–08. Do not rerun the configuration cell, which creates a new run_id. Do not rerun initial table creation.
6. Results appear as DataFrames and charts. Retrieval, coverage, distributions, and candidate CSVs are saved under `work/notebook/queries/<run_id>/report/`.

The default exports 100 episodes at frame-stride=15, yielding about 6,591 frame vectors. Existing
per-frame exports can also be queried. A two-episode smoke test needs a separate S3 / OSS prefix and a
catalog pointing to that export; changing only Notebook parameters is insufficient. Reconfigure
the catalog correctly when returning to the full dataset. Resume exports with the same configuration
and `RESUME_EXPORT=True`. Missing candidates, labels, or training history produce explicit empty
results, not fabricated charts.

The last cell closes the database connection. If you already ran every cell before labeling, set
RUN_SETUP=False and rerun only the connection cell in step 04 before label import and steps 07–08,
keeping the original run_id. SQL statements commit individually; the notebook is not one transaction.
Resume from the appropriate step after a failure.

If a browser cannot play the source AV1 MP4, the notebook still displays a decoded keyframe. The
complete MP4 remains under `work/notebook/media` for viewing with an AV1-compatible player.

## Use it as a presentation page

| Format | Use | Can execute again? |
|---|---|---|
| JupyterLab notebook | Live demonstrations, parameter changes, real results | Yes, with a kernel and data connections |
| Exported HTML | Reports, browser viewing, sharing an execution snapshot | No Python/SQL execution |

Save the notebook's executed outputs, then export:

```bash
mkdir -p work/presentation
jupyter nbconvert notebooks/droid100_end_to_end.en.ipynb \
  --to html --no-input --embed-images --output-dir work/presentation
```

This converts saved content without rerunning expensive processing. Code is hidden; diagrams,
tables, charts, and embedded video remain. Videos increase the file size. Review outputs before
sharing: hiding code does not remove sensitive content from outputs. The
[official nbconvert guide](https://nbconvert.readthedocs.io/en/latest/usage.html) covers HTML and slide exports.

To retain the language switch when sharing HTML, place both HTML files in the same directory.
The packaged pair is named `droid100_preview.html` and `droid100_preview.en.html`. To refresh both
with the same names and relative language links, run from the project root:

```bash
python -m notebooks.export_presentations
```

This helper converts saved outputs only; it does not execute either notebook. If you change a live
notebook, run it and save its outputs first. Download the full repository to browse HTML locally;
GitHub's source view does not render HTML as a live website.

## Validation boundaries

Preview mode was fully executed locally and exported to HTML. SQL/annotation helpers also have
offline tests. With notebook and development dependencies installed, `python -m pytest -q` runs
all 34 tests. Live mode reuses the existing ingestion/query code but has not connected to your
S3 / OSS/Doris environment. The preview must not be presented as a real business report. Source video
and model weights are excluded from the package; see the [validation record](../docs/VALIDATION.md).
