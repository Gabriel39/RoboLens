#!/usr/bin/env python3
"""Export pinned LeRobot DROID-100 data and local embeddings to OSS Lance.

Read Parquet, JSONL, and MP4 directly without installing LeRobot. Preserve all
state/action rows and original video bytes. Compute SigLIP2 frame embeddings;
load CLAP only when an actual audio stream exists. Use ordinary Arrow binary
columns and Lance format 2.1 for Doris compatibility.

Commit episode batches with a multi-table checkpoint, then build cosine vector
indexes for Doris vector_search(). One writer must own the output prefix.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Iterator
from urllib.parse import urlparse

import av
import lance
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

LOG = logging.getLogger("droid_export")
REPO = "lerobot/droid_100"
# Pin dataset and model revisions so moving upstream branches cannot change a run.
SOURCE_REVISION = "78f887947f976d85dd04594bcbd0b05c29893349"
VISION_MODEL = "google/siglip2-base-patch16-224"
VISION_REVISION = "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
AUDIO_MODEL = "laion/clap-htsat-unfused"
AUDIO_REVISION = "8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a"
VISION_DIM, AUDIO_DIM = 768, 512
FORMAT_VERSION = "2.1"
EXPORT_VERSION = 1


def verify_same_data(current, baseline) -> None:
    """Allow index-only version changes, but reject changes to completed export data."""
    if not current.schema.equals(baseline.schema, check_metadata=True):
        raise ValueError("The completed export schema was modified externally")
    before = [f.metadata.to_json() for f in baseline.get_fragments()]
    after = [f.metadata.to_json() for f in current.get_fragments()]
    if before != after:
        raise ValueError("The completed export data fragments changed; refusing to overwrite or index them")


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_relative(value: str) -> str:
    """Reject source paths that escape the working directory."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or not path.parts:
        raise ValueError(f"Unsafe relative path: {value!r}")
    return str(path)


def field_name(name: str) -> str:
    """Replace dots for SQL-friendly names; collisions are checked separately."""
    result = name.replace(".", "_")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", result):
        raise ValueError(f"Field requires an explicit name mapping: {name!r}")
    return result


def unit_vectors(values: np.ndarray, dimension: int) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != dimension or not np.isfinite(values).all():
        raise ValueError(f"Invalid model output: shape={values.shape}, expected=(*,{dimension})")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    if np.any(norms <= 1e-12):
        raise ValueError("The model returned a zero vector")
    return values / norms


def storage_options(output: str, endpoint: str | None, region: str | None) -> dict[str, str]:
    """Configure native OSS access without persisting credentials."""
    scheme = urlparse(output).scheme
    if scheme == "":  # Use local output for smoke tests and oss://bucket/prefix for production.
        return {}
    if scheme != "oss" or not urlparse(output).netloc or not urlparse(output).path.strip("/"):
        raise ValueError("--output must be a local directory or oss://bucket/nonempty-prefix")
    endpoint = endpoint or os.getenv("OSS_ENDPOINT")
    if not endpoint or not endpoint.startswith("https://"):
        raise ValueError("Set --oss-endpoint or OSS_ENDPOINT to an HTTPS endpoint")
    options = {"oss_endpoint": endpoint, "timeout": "300s"}
    keys = {"OSS_ACCESS_KEY_ID": "oss_access_key_id",
            "OSS_ACCESS_KEY_SECRET": "oss_secret_access_key",
            "OSS_SECURITY_TOKEN": "oss_security_token"}
    for env, option in keys.items():
        if os.getenv(env):
            options[option] = os.environ[env]
    if not options.get("oss_access_key_id") or not options.get("oss_secret_access_key"):
        raise ValueError("Set OSS_ACCESS_KEY_ID and OSS_ACCESS_KEY_SECRET")
    if region or os.getenv("OSS_REGION"):
        options["oss_region"] = region or os.environ["OSS_REGION"]
    return options


class Source:
    """Fetch episode files on demand and release temporary downloads after staging."""

    def __init__(self, work: Path, revision: str, local_root: Path | None = None):
        self.work, self.local_root = work, local_root
        self.meta_root = work / "source"
        if local_root is not None:
            self.revision = "local"
            self.meta_root = local_root
        else:
            from huggingface_hub import HfApi, snapshot_download
            api = HfApi()
            self.revision = api.dataset_info(REPO, revision=revision).sha
            snapshot_download(REPO, repo_type="dataset", revision=self.revision,
                              allow_patterns=["meta/**"], local_dir=self.meta_root,
                              max_workers=4)
        self.info = json.loads((self.meta_root / "meta/info.json").read_text())
        if self.info["codebase_version"] != "v2.1":
            raise ValueError("This exporter requires the pinned LeRobot DROID-100 v2.1 layout; v3 is not supported")
        for feature in ("observation.state", "action"):
            if self.info["features"][feature]["shape"] != [7]:
                raise ValueError(f"DROID-100 requires a 7-dimensional {feature}")
        self.cameras = sorted(k for k, f in self.info["features"].items() if f["dtype"] == "video")
        if len(self.cameras) != 3:
            raise ValueError(f"Expected three DROID-100 cameras; found {self.cameras}")
        self.episodes = self.read_jsonl("meta/episodes.jsonl")
        self.episodes.sort(key=lambda x: x["episode_index"])
        ids = [e["episode_index"] for e in self.episodes]
        if ids != list(range(self.info["total_episodes"])):
            raise ValueError("Episode IDs do not match total_episodes")
        if sum(e["length"] for e in self.episodes) != self.info["total_frames"]:
            raise ValueError("Episode lengths do not sum to total_frames")
        self.episode_offsets = {}
        offset = 0
        for episode in self.episodes:
            self.episode_offsets[episode["episode_index"]] = offset
            offset += episode["length"]
        # Resolve task text from tasks.jsonl instead of inferring it from task counts.
        task_rows = self.read_jsonl("meta/tasks.jsonl")
        self.tasks = {x["task_index"]: x["task"] for x in task_rows}
        if len(self.tasks) != len(task_rows):
            raise ValueError("Duplicate task_index in tasks.jsonl")

    def read_jsonl(self, relative: str) -> list[dict]:
        with (self.meta_root / safe_relative(relative)).open() as f:
            return [json.loads(line) for line in f if line.strip()]

    def metadata_files(self) -> list[Path]:
        return sorted(p for p in (self.meta_root / "meta").rglob("*") if p.is_file())

    def metadata_hash(self) -> str:
        h = hashlib.sha256()
        for path in self.metadata_files():
            h.update(str(path.relative_to(self.meta_root)).encode())
            with path.open("rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
        return h.hexdigest()

    def relative(self, episode: int, camera: str | None = None) -> str:
        key = "video_path" if camera else "data_path"
        return safe_relative(self.info[key].format(
            episode_index=episode, episode_chunk=episode // self.info["chunks_size"],
            video_key=camera))

    def fetch(self, relative: str, destination: Path) -> Path:
        relative = safe_relative(relative)
        if self.local_root is not None:
            path = (self.local_root / relative).resolve()
            if not path.is_relative_to(self.local_root.resolve()) or not path.is_file():
                raise FileNotFoundError(relative)
            return path
        from huggingface_hub import hf_hub_download
        return Path(hf_hub_download(REPO, relative, repo_type="dataset",
                                   revision=self.revision, local_dir=destination))


class Encoders:
    """Run local inference and load CLAP only if audio is present."""

    def __init__(self, device: str, cache_dir: Path):
        self.requested_device, self.cache_dir = device, cache_dir
        self.vision = self.audio = None

    def _device(self):
        import torch
        if self.requested_device == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        return self.requested_device

    def image(self, images: list) -> np.ndarray:
        import torch
        from transformers import AutoModel, AutoProcessor
        if self.vision is None:
            self.vision_processor = AutoProcessor.from_pretrained(
                VISION_MODEL, revision=VISION_REVISION, cache_dir=self.cache_dir)
            self.vision = AutoModel.from_pretrained(
                VISION_MODEL, revision=VISION_REVISION, cache_dir=self.cache_dir,
                use_safetensors=True).eval().to(self._device())
            LOG.info("SigLIP2 loaded, device=%s", self._device())
        inputs = self.vision_processor(images=images, return_tensors="pt").to(self._device())
        with torch.inference_mode():
            result = self.vision.get_image_features(**inputs)
        return unit_vectors(result.float().cpu().numpy(), VISION_DIM)

    def sound(self, samples: np.ndarray) -> np.ndarray:
        import torch
        from transformers import ClapModel, ClapProcessor
        if self.audio is None:
            self.audio_processor = ClapProcessor.from_pretrained(
                AUDIO_MODEL, revision=AUDIO_REVISION, cache_dir=self.cache_dir)
            self.audio = ClapModel.from_pretrained(
                AUDIO_MODEL, revision=AUDIO_REVISION, cache_dir=self.cache_dir,
                # This pinned revision provides pytorch_model.bin; require torch >= 2.6
                # and weights_only loading instead of remote safetensors conversion.
                use_safetensors=False, weights_only=True).eval().to(self._device())
        # The CLAP processor pads the final window when it is shorter than ten seconds.
        inputs = self.audio_processor(audios=[samples], sampling_rate=48000,
                                      return_tensors="pt").to(self._device())
        with torch.inference_mode():
            result = self.audio.get_audio_features(**inputs)
        return unit_vectors(result.float().cpu().numpy(), AUDIO_DIM)[0]


def schemas(source_schema: pa.Schema) -> dict[str, pa.Schema]:
    fields = []
    names = set()
    for f in source_schema:
        name = field_name(f.name)
        if name in names or name in {"task", "source_parquet"}:
            raise ValueError(f"Renamed field collision: {name}")
        names.add(name)
        dtype = f.type
        # Preserve source numeric types and remove only pandas/HF schema metadata.
        if isinstance(dtype, pa.BaseExtensionType) or pa.types.is_dictionary(dtype):
            raise ValueError(f"Unsupported source type for Doris: {f}")
        fields.append(pa.field(name, dtype))
    fields += [pa.field("task", pa.string()), pa.field("source_parquet", pa.string())]
    return {
        "frames": pa.schema(fields),
        "episodes": pa.schema([
            ("episode_index", pa.int64()), ("length", pa.int64()),
            ("tasks", pa.list_(pa.string())), ("metadata_json", pa.string())]),
        "media": pa.schema([
            ("media_id", pa.string()), ("episode_index", pa.int64()),
            ("camera", pa.string()), ("source_path", pa.string()),
            ("sha256", pa.string()), ("byte_size", pa.int64()),
            ("frame_count", pa.int64()), ("fps", pa.float64()),
            ("duration_s", pa.float64()), ("audio_stream_count", pa.int32()),
            ("video_bytes", pa.large_binary()),
            ("video_embedding", pa.list_(pa.float32(), VISION_DIM)),
            ("embedding_frame_count", pa.int64()),
            ("embedding_model", pa.string()), ("embedding_revision", pa.string())]),
        "frame_embeddings": pa.schema([
            ("sample_id", pa.string()), ("index", pa.int64()),
            ("episode_index", pa.int64()), ("frame_index", pa.int64()),
            ("timestamp", pa.float64()), ("video_timestamp", pa.float64()),
            ("camera", pa.string()), ("media_id", pa.string()),
            ("embedding", pa.list_(pa.float32(), VISION_DIM)),
            ("model", pa.string()), ("model_revision", pa.string())]),
        "audio_embeddings": pa.schema([
            ("sample_id", pa.string()), ("episode_index", pa.int64()),
            ("media_id", pa.string()), ("camera", pa.string()),
            ("stream_index", pa.int32()), ("start_s", pa.float64()),
            ("end_s", pa.float64()), ("sample_rate", pa.int32()),
            ("embedding", pa.list_(pa.float32(), AUDIO_DIM)),
            ("model", pa.string()), ("model_revision", pa.string())]),
        "metadata": pa.schema([("path", pa.string()), ("data", pa.large_binary())]),
    }


class Stager:
    """Stage Arrow IPC batches on disk instead of retaining the entire export in RAM."""

    def __init__(self, root: Path, table_schemas: dict[str, pa.Schema]):
        root.mkdir(parents=True, exist_ok=True)
        self.schemas, self.paths, self.writers = table_schemas, {}, {}
        self.counts = {name: 0 for name in table_schemas}
        self.stack = ExitStack()
        for name, schema in table_schemas.items():
            path = root / f"{name}.arrow"
            self.paths[name] = path
            sink = self.stack.enter_context(pa.OSFile(str(path), "wb"))
            self.writers[name] = self.stack.enter_context(pa.ipc.new_stream(sink, schema))

    def add(self, name: str, rows) -> None:
        table = rows if isinstance(rows, pa.Table) else pa.Table.from_pylist(rows, schema=self.schemas[name])
        table = table.cast(self.schemas[name])
        self.writers[name].write_table(table, max_chunksize=1024)
        self.counts[name] += table.num_rows

    def close(self):
        self.stack.close()


class ExportStore:
    """Checkpoint multi-table writes made by this single-writer exporter."""

    def __init__(self, output: str, options: dict, config: dict,
                 table_schemas: dict[str, pa.Schema], resume: bool):
        self.output, self.options = output.rstrip("/"), options
        self.fingerprint = digest(json_text(config).encode())
        self.schemas = {name: schema.with_metadata({
            b"droid_export_fingerprint": self.fingerprint.encode()})
            for name, schema in table_schemas.items()}
        run_schema = pa.schema([("config_json", pa.string())])
        run = self.open_optional("export_manifest")
        if run is not None:
            existing = run.to_table().to_pylist()
            if existing != [{"config_json": json_text(config)}]:
                raise ValueError("Output belongs to a different configuration or revision; use a new prefix")
            if not resume:
                raise FileExistsError("Output already exists; use --resume to continue")
        else:
            self.write("export_manifest", pa.Table.from_pylist(
                [{"config_json": json_text(config)}], schema=run_schema), "create")
        for name, schema in self.schemas.items():
            ds = self.open_optional(name)
            if ds is None:
                self.write(name, pa.Table.from_batches([], schema=schema), "create")
            elif ds.schema.metadata != schema.metadata or not ds.schema.equals(schema):
                raise ValueError(f"{name} schema or export fingerprint does not match")
        self.checkpoint_schema = pa.schema([
            ("next_episode_offset", pa.int64()), ("versions_json", pa.string()),
            ("complete", pa.bool_()), ("fingerprint", pa.string())])
        checkpoint = self.open_optional("export_checkpoints")
        if checkpoint is None:
            # No data is appended before the initial checkpoint, so all tables must be empty.
            if any(self.open(name).count_rows() for name in self.schemas):
                raise ValueError("Missing initial checkpoint with nonempty data; refusing to guess recovery state")
            self.save_checkpoint(0, False, mode="create")
            checkpoint = self.open("export_checkpoints")
        records = checkpoint.to_table().to_pylist()
        last = records[-1]
        if last["fingerprint"] != self.fingerprint:
            raise ValueError("Checkpoint configuration fingerprint does not match")
        self.next_offset, self.complete = last["next_episode_offset"], last["complete"]
        # Restore an interrupted data batch to its last complete checkpoint.
        for name, version in json.loads(last["versions_json"]).items():
            current = self.open(name)
            if current.version != version:
                old = lance.dataset(self.uri(name), version=version, storage_options=self.options)
                if self.complete:
                    # Preserve committed indexes after the data stage has completed.
                    verify_same_data(current, old)
                    continue
                LOG.warning("Restoring %s to committed snapshot version=%s", name, version)
                old.restore()

    def uri(self, name: str) -> str:
        return f"{self.output}/{name}.lance"

    def open(self, name: str):
        return lance.dataset(self.uri(name), storage_options=self.options)

    def open_optional(self, name: str):
        try:
            return self.open(name)
        except FileNotFoundError:
            return None
        except ValueError as exc:
            # PyLance 11 maps DatasetNotFound to ValueError. Match only an explicit
            # missing-dataset error; propagate authentication, network, and corruption errors.
            if re.match(r"^Dataset at path .+ was not found: Not found:", str(exc)):
                return None
            raise

    def write(self, name, data, mode="append", schema=None):
        return lance.write_dataset(data, self.uri(name), mode=mode, schema=schema,
                                   storage_options=self.options,
                                   data_storage_version=FORMAT_VERSION,
                                   max_rows_per_file=1024 * 1024)

    def save_checkpoint(self, next_offset: int, complete: bool, mode="append"):
        versions = {name: self.open(name).version for name in self.schemas}
        row = {"next_episode_offset": next_offset, "versions_json": json_text(versions),
               "complete": complete, "fingerprint": self.fingerprint}
        self.write("export_checkpoints", pa.Table.from_pylist([row], schema=self.checkpoint_schema), mode)

    def commit(self, staged: Stager, next_offset: int, complete: bool):
        for name in self.schemas:
            if staged.counts[name]:
                with pa.memory_map(str(staged.paths[name]), "r") as f:
                    reader = pa.ipc.open_stream(f)
                    # Restore the schema fingerprint used to validate resumed writes.
                    batches = (b.replace_schema_metadata(self.schemas[name].metadata) for b in reader)
                    self.write(name, batches, schema=self.schemas[name])
        self.save_checkpoint(next_offset, complete)
        self.next_offset, self.complete = next_offset, complete


def audio_windows(path: Path, stream_index: int) -> Iterator[tuple[float, float, np.ndarray]]:
    """Resample audio to 48 kHz mono windows; preserve original bytes in media."""
    with av.open(str(path)) as container:
        stream = container.streams[stream_index]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=48000)
        pending = np.empty(0, dtype=np.float32)
        origin = None
        emitted = 0
        received = 0
        def converted_frames():
            for frame in container.decode(stream):
                yield from resampler.resample(frame)
            yield from resampler.resample(None)
        for frame in converted_frames():
            if frame.time is None:
                raise ValueError("Audio has no PTS for reliable video alignment")
            if origin is None:
                origin = frame.time
            if abs(frame.time - (origin + received / 48000)) > 0.01:
                raise ValueError("Discontinuous audio timestamps; refusing to concatenate misaligned audio")
            samples = frame.to_ndarray().reshape(-1).astype(np.float32)
            received += len(samples)
            pending = np.concatenate([pending, samples])
            while len(pending) >= 480000:
                yield origin + emitted / 48000, origin + (emitted + 480000) / 48000, pending[:480000]
                emitted += 480000
                pending = pending[480000:]
        if len(pending):
            yield origin + emitted / 48000, origin + (emitted + len(pending)) / 48000, pending


def transform_video(path: Path, relative: str, camera: str, episode: int,
                    frames: pa.Table, encoders, staged: Stager, stride: int,
                    batch_size: int, tolerance: float) -> None:
    media_id = f"{episode}:{camera}"
    timestamps = frames["timestamp"].to_numpy()
    global_indices = frames["index"].to_numpy()
    rows, images = [], []
    vector_sum = np.zeros(VISION_DIM, dtype=np.float64)
    vector_count = 0

    def flush():
        nonlocal vector_count
        if not images:
            return
        embeddings = unit_vectors(encoders.image(images), VISION_DIM)
        if len(embeddings) != len(rows):
            raise ValueError("Image and embedding batch lengths differ")
        for row, vector in zip(rows, embeddings, strict=True):
            row["embedding"] = vector.tolist()
            vector_sum[:] += vector
        vector_count += len(rows)
        staged.add("frame_embeddings", rows)
        rows.clear()
        images.clear()

    count = 0
    with av.open(str(path)) as container:
        if len(container.streams.video) != 1:
            raise ValueError(f"Expected exactly one video stream: {relative}")
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        fps = float(stream.average_rate or 0)
        if fps <= 0:
            raise ValueError(f"Invalid fps: {relative}")
        audio_streams = [s.index for s in container.streams.audio]
        first_pts = None
        last_pts = 0.0
        for ordinal, frame in enumerate(container.decode(stream)):
            if ordinal >= len(timestamps):
                raise ValueError(f"Video frame count exceeds state row count: {relative}")
            if frame.time is None:
                raise ValueError(f"Video has no PTS: {relative}")
            pts = float(frame.time)
            if first_pts is None:
                first_pts = pts
            relative_pts = pts - first_pts
            if abs(relative_pts - float(timestamps[ordinal])) > tolerance:
                raise ValueError(f"Video/state timestamp mismatch: {relative}, frame={ordinal}, "
                                 f"video={relative_pts}, state={timestamps[ordinal]}")
            last_pts = relative_pts
            count += 1
            if ordinal % stride:
                continue
            images.append(frame.to_image().convert("RGB"))
            rows.append({"sample_id": f"{media_id}:{ordinal}",
                         "index": int(global_indices[ordinal]), "episode_index": episode,
                         "frame_index": ordinal, "timestamp": float(timestamps[ordinal]),
                         "video_timestamp": pts, "camera": camera, "media_id": media_id,
                         "model": VISION_MODEL, "model_revision": VISION_REVISION})
            if len(images) >= batch_size:
                flush()
        flush()
    if count != len(timestamps) or count == 0:
        raise ValueError(f"Video frame count {count} differs from state row count {len(timestamps)}: {relative}")
    # Pool frame vectors; this representation does not encode temporal action order.
    pooled = unit_vectors((vector_sum / vector_count)[None, :], VISION_DIM)[0]
    payload = path.read_bytes()
    staged.add("media", [{"media_id": media_id, "episode_index": episode, "camera": camera,
                         "source_path": relative, "sha256": digest(payload), "byte_size": len(payload),
                         "frame_count": count, "fps": fps, "duration_s": last_pts + 1 / fps,
                         "audio_stream_count": len(audio_streams), "video_bytes": payload,
                         "video_embedding": pooled.tolist(), "embedding_frame_count": vector_count,
                         "embedding_model": VISION_MODEL, "embedding_revision": VISION_REVISION}])
    del payload
    for stream_index in audio_streams:
        for clip_index, (start, end, samples) in enumerate(audio_windows(path, stream_index)):
            vector = unit_vectors(np.asarray(encoders.sound(samples))[None, :], AUDIO_DIM)[0]
            staged.add("audio_embeddings", [{
                "sample_id": f"{media_id}:audio:{stream_index}:{clip_index}",
                "episode_index": episode, "media_id": media_id, "camera": camera,
                "stream_index": stream_index, "start_s": start, "end_s": end,
                "sample_rate": 48000, "embedding": vector.tolist(),
                "model": AUDIO_MODEL, "model_revision": AUDIO_REVISION}])


def read_frames(source: Source, episode: dict, destination: Path) -> tuple[pa.Table, str]:
    ep = episode["episode_index"]
    relative = source.relative(ep)
    table = pq.read_table(source.fetch(relative, destination))
    required = {"observation.state", "action", "timestamp", "frame_index", "episode_index", "index", "task_index"}
    if not required.issubset(table.column_names):
        raise ValueError(f"Missing source columns: {required - set(table.column_names)}")
    if table.num_rows != episode["length"] or table.num_rows == 0:
        raise ValueError(f"episode {ep}: Row count differs from metadata.length")
    if not np.all(table["episode_index"].to_numpy() == ep):
        raise ValueError(f"episode {ep}: Found rows from another episode")
    if not np.array_equal(table["frame_index"].to_numpy(), np.arange(table.num_rows)):
        raise ValueError(f"episode {ep}: frame_index must be contiguous and start at zero")
    first_index = source.episode_offsets[ep]
    if not np.array_equal(table["index"].to_numpy(), np.arange(first_index, first_index + table.num_rows)):
        raise ValueError(f"episode {ep}: Global index does not match episode boundaries")
    ts = table["timestamp"].to_numpy()
    if not np.isfinite(ts).all() or ts[0] != 0 or np.any(np.diff(ts) <= 0):
        raise ValueError(f"episode {ep}: timestamp must be finite, increasing, and start at zero")
    for key in ("observation.state", "action"):
        expected = source.info["features"][key]["shape"][0]
        if any(len(row) != expected for row in table[key].to_pylist()):
            raise ValueError(f"{key} dimension differs from info.json")
    return table, relative


def export(source: Source, output: str, options: dict, work: Path, encoders,
           start_episode: int = 0, max_episodes: int | None = None,
           episodes_per_commit: int = 16, frame_stride: int = 1,
           embedding_batch_size: int = 32, timestamp_tolerance: float = 0.04,
           resume: bool = False) -> dict[str, int]:
    selected = [e for e in source.episodes if e["episode_index"] >= start_episode]
    if max_episodes is not None:
        selected = selected[:max_episodes]
    if not selected:
        raise ValueError("No episodes selected")
    with tempfile.TemporaryDirectory(dir=work, prefix="schema-") as tmp:
        first, _ = read_frames(source, selected[0], Path(tmp))
        table_schemas = schemas(first.schema)
    config = {"export_version": EXPORT_VERSION, "repo": REPO, "source_revision": source.revision,
              "source_metadata_sha256": source.metadata_hash(), "format": FORMAT_VERSION,
              "start_episode": start_episode, "selected_episodes": len(selected),
              "last_episode": selected[-1]["episode_index"], "frame_stride": frame_stride,
              "timestamp_tolerance": timestamp_tolerance,
              "vision_model": VISION_MODEL, "vision_revision": VISION_REVISION,
              "audio_model": AUDIO_MODEL, "audio_revision": AUDIO_REVISION,
              "video_pooling": "l2_normalized_mean_of_l2_frame_vectors",
              "audio_sample_rate": 48000, "audio_window_seconds": 10,
              "source_columns": {f.name: field_name(f.name) for f in first.schema}}
    store = ExportStore(output, options, config, table_schemas, resume)
    if store.complete:
        LOG.info("Data export is already complete; reusing existing results")
    for offset in range(store.next_offset, len(selected), episodes_per_commit):
        group = selected[offset:offset + episodes_per_commit]
        with tempfile.TemporaryDirectory(dir=work, prefix="batch-") as tmp:
            root = Path(tmp)
            staged = Stager(root / "arrow", table_schemas)
            try:
                if offset == 0:
                    for path in source.metadata_files():
                        staged.add("metadata", [{"path": str(path.relative_to(source.meta_root)),
                                                 "data": path.read_bytes()}])
                for group_index, episode in enumerate(group):
                    ep = episode["episode_index"]
                    LOG.info("Processing episode=%d (%d/%d)", ep, offset + group_index + 1, len(selected))
                    # Keep only the current episode download; retain processed batches in IPC files.
                    with tempfile.TemporaryDirectory(dir=root, prefix=f"ep-{ep}-") as ep_tmp:
                        dest = Path(ep_tmp)
                        original, relative = read_frames(source, episode, dest)
                        renamed = original.rename_columns([field_name(k) for k in original.column_names])
                        tasks = [source.tasks[int(k)] for k in original["task_index"].to_pylist()]
                        renamed = renamed.append_column("task", pa.array(tasks, pa.string()))
                        renamed = renamed.append_column("source_parquet", pa.array([relative] * len(tasks)))
                        staged.add("frames", renamed)
                        staged.add("episodes", [{"episode_index": ep, "length": episode["length"],
                                                  "tasks": episode.get("tasks", []),
                                                  "metadata_json": json_text(episode)}])
                        for camera in source.cameras:
                            relative_video = source.relative(ep, camera)
                            video = source.fetch(relative_video, dest)
                            transform_video(video, relative_video, camera, ep, original, encoders,
                                            staged, frame_stride, embedding_batch_size, timestamp_tolerance)
                staged.close()
                next_offset = offset + len(group)
                store.commit(staged, next_offset, complete=next_offset == len(selected))
                LOG.info("Committed %d/%d episodes; batch rows=%s", next_offset, len(selected), staged.counts)
            finally:
                staged.close()
    counts = {name: store.open(name).count_rows() for name in table_schemas}
    expected_frames = sum(e["length"] for e in selected)
    expected_embeddings = sum((e["length"] + frame_stride - 1) // frame_stride for e in selected) * len(source.cameras)
    expected = {"frames": expected_frames, "episodes": len(selected),
                "media": len(selected) * len(source.cameras), "frame_embeddings": expected_embeddings,
                "metadata": len(source.metadata_files())}
    if any(counts[k] != n for k, n in expected.items()):
        raise ValueError(f"Final row count validation failed: expected={expected}, actual={counts}")
    LOG.info("Data export complete: %s", json_text(counts))
    return counts


def build_indexes(output: str, options: dict, *, index_type: str = "IVF_PQ",
                  target_partition_size: int = 8192, num_sub_vectors: int = 64,
                  shuffle_partition_batches: int = 32) -> dict:
    """Build cosine indexes in the existing Lance datasets without copying vectors."""
    if index_type not in {"IVF_PQ", "IVF_FLAT"}:
        raise ValueError("index_type must be IVF_PQ or IVF_FLAT")
    if target_partition_size <= 0 or shuffle_partition_batches <= 0:
        raise ValueError("Index partition and shuffle batch sizes must be positive")
    if num_sub_vectors <= 0 or any(d % num_sub_vectors for d in (VISION_DIM, AUDIO_DIM)):
        raise ValueError("PQ subvector count must be a positive divisor of 768 and 512")
    # Exclude large centroid arrays from index statistics.
    os.environ["LANCE_INCLUDE_VECTOR_CENTROIDS"] = "false"
    root = output.rstrip("/")

    def open_table(name, version=None):
        return lance.dataset(f"{root}/{name}.lance", version=version, storage_options=options)

    checkpoint = open_table("export_checkpoints").to_table().to_pylist()[-1]
    config_json = open_table("export_manifest").to_table()["config_json"][0].as_py()
    if not checkpoint["complete"] or checkpoint["fingerprint"] != digest(config_json.encode()):
        raise ValueError("Data export is incomplete or checkpoint does not match; finish exporting first")
    # Validate completed snapshots without contacting Hugging Face in index-only mode.
    for name, version in json.loads(checkpoint["versions_json"]).items():
        verify_same_data(open_table(name), open_table(name, version))

    summary = {}
    for table, column, dim in [("frame_embeddings", "embedding", VISION_DIM),
                               ("media", "video_embedding", VISION_DIM),
                               ("audio_embeddings", "embedding", AUDIO_DIM)]:
        ds = open_table(table)
        rows = ds.count_rows()
        if not rows:
            summary[table] = {"state": "empty", "rows": 0}
            LOG.info("%s is empty; skipping its vector index", table)
            continue
        actual_type = "IVF_FLAT" if rows < 256 else index_type
        plan = {"column": column, "dimension": dim, "metric": "cosine",
                "index_type": actual_type, "target_partition_size": target_partition_size}
        if actual_type == "IVF_PQ":
            plan["num_sub_vectors"] = num_sub_vectors
        # Include the index configuration digest in its name for idempotent recovery.
        prefix = f"droid_{column}_cosine_"
        name = prefix + digest(json_text(plan).encode())[:16]
        indices = ds.list_indices()
        if any(i["name"].startswith(prefix) and i["name"] != name for i in indices):
            raise ValueError(f"{table} has an index with different parameters; reuse the original parameters")
        found = next((i for i in indices if i["name"] == name), None)
        if found is None:
            LOG.info("Building index %s.%s: rows=%d, type=%s, metric=cosine",
                     table, column, rows, actual_type)
            kwargs = {"target_partition_size": target_partition_size,
                      # Bound shuffle batches, while reserving additional memory for training samples
                      # and centroids; this is not a strict process-wide memory limit.
                      "shuffle_partition_batches": shuffle_partition_batches,
                      "shuffle_partition_concurrency": 2}
            if actual_type == "IVF_PQ":
                kwargs["num_sub_vectors"] = num_sub_vectors
            ds.create_index(column, index_type=actual_type, name=name, metric="cosine",
                            replace=False, storage_options=options, **kwargs)
            # Read the committed manifest even if the previous client lost its response.
            ds = open_table(table)
            found = next((i for i in ds.list_indices() if i["name"] == name), None)
        if found is None or found["fields"] != [column] or found["type"] != actual_type:
            raise ValueError(f"{table} index metadata does not match the requested configuration")
        stats = ds.stats.index_stats(name)
        segments = stats.get("indices", [])
        if (stats.get("num_unindexed_rows") != 0 or stats.get("num_indexed_rows") != rows
                or not segments or any(s.get("metric_type", "").lower() != "cosine" for s in segments)):
            raise ValueError(f"{table} index coverage is incomplete or the metric does not match")
        summary[table] = {"state": "ready", "rows": rows, "name": name,
                          "uuid": found["uuid"], "plan": plan}
        LOG.info("Index ready for %s: %s, covering %d rows", table, name, rows)

    # Publish readiness only after every nonempty vector table has a validated index.
    # A retry reuses committed indexes when the status write has not completed.
    report_schema = pa.schema([("source_fingerprint", pa.string()),
                              ("indexes_json", pa.string()), ("complete", pa.bool_())])
    report = {"source_fingerprint": checkpoint["fingerprint"],
              "indexes_json": json_text(summary), "complete": True}
    uri = f"{root}/export_index_status.lance"
    try:
        existing = open_table("export_index_status")
    except FileNotFoundError:
        existing = None
    except ValueError as exc:
        if not re.match(r"^Dataset at path .+ was not found: Not found:", str(exc)):
            raise
        existing = None
    if existing is None or existing.to_table().to_pylist() != [report]:
        # Do not silently replace readiness metadata for a different set of indexes.
        if existing is not None:
            raise ValueError("Index status differs from actual indexes; check for external modifications")
        lance.write_dataset(pa.Table.from_pylist([report], schema=report_schema), uri,
                            mode="create", storage_options=options,
                            data_storage_version=FORMAT_VERSION)
    return summary


@contextmanager
def local_lock(work: Path):
    """Prevent concurrent use of one work directory; this is not a distributed lock."""
    with (work / "export.lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise RuntimeError("Another exporter is using this work directory") from e
        yield


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True, help="oss://bucket/prefix or a local test directory")
    parser.add_argument("--oss-endpoint", help="For example https://oss-cn-hangzhou.aliyuncs.com")
    parser.add_argument("--oss-region", help="For example cn-hangzhou")
    parser.add_argument("--work-dir", type=Path, default=Path("./work"), help="Model cache and batch working directory")
    parser.add_argument("--revision", default=SOURCE_REVISION, help="Hugging Face revision; defaults to the verified v2.1 commit")
    parser.add_argument("--source-root", type=Path, help="Optional local copy of lerobot/droid_100 v2.1")
    parser.add_argument("--start-episode", type=int, default=0)
    parser.add_argument("--max-episodes", type=int, help="Limit smoke-test episodes; omit to export all remaining episodes")
    parser.add_argument("--episodes-per-commit", type=int, default=16, help="Episodes per commit; controls staging size and recovery work")
    parser.add_argument("--frame-stride", type=int, default=1, help="Embedding sampling only: 1=every frame, 15=about 1 Hz at 15 fps")
    parser.add_argument("--embedding-batch-size", type=int, default=32)
    parser.add_argument("--timestamp-tolerance", type=float, default=0.04, help="Maximum video/state timestamp error in seconds")
    parser.add_argument("--device", default="auto", help="auto / cpu / cuda / cuda:0")
    parser.add_argument("--resume", action="store_true", help="Resume from the last complete data checkpoint and preserve completed indexes")
    parser.add_argument("--index-only", action="store_true", help="Build indexes for a completed export without loading source data or models")
    parser.add_argument("--skip-indexes", action="store_true", help="Skip indexing now; run --index-only later")
    parser.add_argument("--index-type", choices=["IVF_PQ", "IVF_FLAT"], default="IVF_PQ")
    parser.add_argument("--index-target-partition-size", type=int, default=8192,
                        help="Target vectors per IVF partition; default 8192")
    parser.add_argument("--index-num-sub-vectors", type=int, default=64, help="PQ subvector count; default 64")
    parser.add_argument("--index-shuffle-batches", type=int, default=32, help="Batches per index shuffle work unit")
    args = parser.parse_args()
    for key in ("episodes_per_commit", "frame_stride", "embedding_batch_size",
                "index_target_partition_size", "index_shuffle_batches", "index_num_sub_vectors"):
        if getattr(args, key) <= 0:
            parser.error(f"--{key.replace('_', '-')} must be positive")
    if args.max_episodes is not None and args.max_episodes <= 0:
        parser.error("--max-episodes must be positive")
    if args.start_episode < 0 or not 0 <= args.timestamp_tolerance < 1 / 15:
        parser.error("start-episode must be nonnegative; timestamp-tolerance must be in [0,1/15)")
    if any(d % args.index_num_sub_vectors for d in (VISION_DIM, AUDIO_DIM)):
        parser.error("--index-num-sub-vectors must divide both 768 and 512")
    if args.index_only and args.skip_indexes:
        parser.error("--index-only and --skip-indexes are mutually exclusive")
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    # Set cache locations before importing Hugging Face and Transformers.
    os.environ.setdefault("HF_HOME", str(work / "hf"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TMPDIR", str(work))
    options = storage_options(args.output, args.oss_endpoint, args.oss_region)
    output = args.output if urlparse(args.output).scheme else str(Path(args.output).resolve())
    with local_lock(work):
        if not args.index_only:
            source = Source(work, args.revision, args.source_root.resolve() if args.source_root else None)
            encoders = Encoders(args.device, work / "models")
            export(source, output, options, work, encoders, args.start_episode, args.max_episodes,
                   args.episodes_per_commit, args.frame_stride, args.embedding_batch_size,
                   args.timestamp_tolerance, args.resume)
        if not args.skip_indexes:
            build_indexes(output, options, index_type=args.index_type,
                          target_partition_size=args.index_target_partition_size,
                          num_sub_vectors=args.index_num_sub_vectors,
                          shuffle_partition_batches=args.index_shuffle_batches)
            LOG.info("Data and indexes are ready for Doris vector_search()")


if __name__ == "__main__":
    main()
