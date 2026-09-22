"""Extract one original MP4 from media.lance for human review."""
import argparse
from pathlib import Path
import re

import lance

from pipeline.export_droid import digest, storage_options


def extract(root: str, media_id: str, destination: Path, options: dict) -> None:
    """Validate the media key and SHA256 before writing the preserved original bytes."""
    if not re.fullmatch(r"[0-9]+:observation\.images\.(wrist_image_left|exterior_image_[12]_left)", media_id):
        raise ValueError("Invalid media_id")
    ds = lance.dataset(root.rstrip("/") + "/media.lance", storage_options=options)
    rows = ds.to_table(filter=f"media_id = '{media_id}'", columns=["video_bytes", "sha256"]).to_pylist()
    if len(rows) != 1:
        raise ValueError(f"Expected one media row, found {len(rows)}")
    payload = rows[0]["video_bytes"]
    if digest(payload) != rows[0]["sha256"]:
        raise ValueError("Video SHA256 verification failed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as file:
        file.write(payload)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True)
    parser.add_argument("--media-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--oss-endpoint")
    parser.add_argument("--oss-region")
    args = parser.parse_args()
    options = storage_options(args.dataset_root, args.oss_endpoint, args.oss_region)
    extract(args.dataset_root, args.media_id, args.output, options)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
