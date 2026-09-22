"""Render Vector Search and analysis SQL using a real 768-dimensional query vector.

This utility writes SQL files only. It never connects to Doris or executes SQL.
Use an existing exported frame or encode a new local reference image with the
same pinned SigLIP2 model as the ingestion pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re

import lance
import numpy as np

from pipeline.export_droid import Encoders, VISION_DIM, VISION_MODEL, VISION_REVISION, add_storage_arguments, storage_options

CAMERAS = {
    "wrist": "observation.images.wrist_image_left",
    "exterior1": "observation.images.exterior_image_1_left",
    "exterior2": "observation.images.exterior_image_2_left",
}
# Explicit full camera names avoid ambiguous or SQL-bearing identifiers.
SAMPLE_PATTERN = r"[0-9]+:(?:" + "|".join(re.escape(c) for c in CAMERAS.values()) + r"):[0-9]+"


def read_reference(dataset_root: str, sample_id: str, options: dict) -> dict:
    """Read exactly one existing frame without recomputing its embedding."""
    if not re.fullmatch(SAMPLE_PATTERN, sample_id):
        raise ValueError("Invalid sample_id; use episode:camera:frame_index")
    ds = lance.dataset(dataset_root.rstrip("/") + "/frame_embeddings.lance", storage_options=options)
    rows = ds.to_table(filter=f"sample_id = '{sample_id}'", columns=[
        "sample_id", "episode_index", "camera", "embedding", "model", "model_revision"]).to_pylist()
    if len(rows) != 1:
        raise ValueError(f"Expected one exported reference frame, found {len(rows)}")
    if (rows[0]["model"], rows[0]["model_revision"]) != (VISION_MODEL, VISION_REVISION):
        raise ValueError("Reference model differs from the pinned query model")
    return rows[0]


def render(reference: dict, output_dir: Path, run_id: str) -> list[Path]:
    """Validate inputs before writing a fresh, immutable set of query templates."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ValueError("run-id must contain 1-64 letters, digits, underscores, or hyphens")
    vector = np.asarray(reference["embedding"], dtype=np.float64)
    if vector.shape != (VISION_DIM,) or not np.isfinite(vector).all():
        raise ValueError("Query vector must contain 768 finite numbers")
    if not np.isclose(np.linalg.norm(vector), 1.0, atol=1e-4):
        raise ValueError("Query vector must be L2-normalized")
    if reference["camera"] not in CAMERAS.values():
        raise ValueError("Unsupported camera")
    episode = reference["episode_index"]
    if isinstance(episode, bool) or not isinstance(episode, int) or episode < -1:
        raise ValueError("Invalid reference episode")
    tokens = {"{{QUERY_VECTOR_JSON}}": json.dumps(vector.tolist(), allow_nan=False),
              "{{REFERENCE_EPISODE}}": str(episode), "{{CAMERA}}": reference["camera"],
              "{{RUN_ID}}": run_id}
    rendered = {}
    for path in sorted((Path(__file__).parent / "sql").glob("0[2-5]_*.sql")):
        text = path.read_text()
        for token, value in tokens.items():
            text = text.replace(token, value)
        if "{{" in text or "REPLACE_" in text:
            raise ValueError(f"Unresolved SQL placeholder in {path.name}")
        rendered[path.name] = text
    if len(rendered) != 4:
        raise ValueError("Expected four analysis templates")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("Use a fresh output directory for each query run")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, text in rendered.items():
        (output_dir / name).write_text(text)
    # Keep the actual reference and source model for reproducibility, never credentials.
    manifest = {**reference, "embedding": vector.tolist(), "run_id": run_id}
    (output_dir / "query.json").write_text(json.dumps(manifest, indent=2, allow_nan=False))
    return [output_dir / name for name in rendered]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sample-id", help="An existing exported episode:camera:frame_index")
    source.add_argument("--image", type=Path, help="A new local RGB reference image")
    parser.add_argument("--dataset-root", help="Export root required with --sample-id")
    add_storage_arguments(parser)
    parser.add_argument("--camera", choices=CAMERAS, default="wrist", help="Camera view of --image")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--work-dir", type=Path, default=Path("work/query-cache"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    if args.sample_id:
        if not args.dataset_root:
            parser.error("--sample-id requires --dataset-root")
        options = storage_options(args.dataset_root, args.oss_endpoint, args.oss_region,
                                  s3_endpoint=args.s3_endpoint, s3_region=args.s3_region)
        reference = read_reference(args.dataset_root, args.sample_id, options)
    else:
        from PIL import Image
        work = args.work_dir.resolve()
        work.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(work / "hf"))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        encoder = Encoders(args.device, work / "models")
        with Image.open(args.image) as image:
            vector = encoder.image([image.convert("RGB")])[0]
        reference = {"episode_index": -1, "camera": CAMERAS[args.camera],
                     "embedding": vector.tolist(), "model": VISION_MODEL,
                     "model_revision": VISION_REVISION, "image": str(args.image.resolve())}
    for path in render(reference, args.output_dir, args.run_id):
        print(path.resolve())


if __name__ == "__main__":
    main()
