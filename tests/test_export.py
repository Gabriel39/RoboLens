"""Offline integration tests with real media/Lance and explicit model stubs.

These tests validate data transport and indexing, not model semantics.
The production CLI has no mock-embedding mode.
"""
from fractions import Fraction
import json
from pathlib import Path
import sys

import av
import lance
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import export_droid as app


def test_missing_dataset_detection_does_not_swallow_other_errors(monkeypatch):
    store = app.ExportStore.__new__(app.ExportStore)
    for message in ["AccessDenied", "connection timed out", "invalid manifest"]:
        def fail(name, text=message):
            raise ValueError(text)
        monkeypatch.setattr(store, "open", fail)
        with pytest.raises(ValueError, match=message):
            store.open_optional("frames")


class TestEncoders:
    __test__ = False

    def __init__(self):
        self.image_calls = 0
        self.audio_calls = 0

    def image(self, images):
        self.image_calls += 1
        result = np.ones((len(images), app.VISION_DIM), np.float32)
        for i, image in enumerate(images):
            result[i, 0] = float(np.asarray(image).mean()) + 1
        return app.unit_vectors(result, app.VISION_DIM)

    def sound(self, samples):
        self.audio_calls += 1
        assert len(samples) <= 480000
        return app.unit_vectors(np.ones((1, app.AUDIO_DIM)), app.AUDIO_DIM)[0]


def write_video(path, count=4):
    path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=15)
        stream.width = stream.height = 32
        stream.pix_fmt = "yuv420p"
        for i in range(count):
            array = np.full((32, 32, 3), 20 + i * 40, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            frame.pts = i
            frame.time_base = Fraction(1, 15)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "meta").mkdir(parents=True)
    cameras = [f"observation.images.{name}" for name in ["exterior_image_1_left", "exterior_image_2_left", "wrist_image_left"]]
    info = {"codebase_version": "v2.1", "total_episodes": 2, "total_frames": 8,
            "chunks_size": 1000, "fps": 15,
            "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            "video_path": "videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4",
            "features": {**{k: {"dtype": "video"} for k in cameras},
                         "observation.state": {"dtype": "float32", "shape": [7]},
                         "action": {"dtype": "float32", "shape": [7]}}}
    (root / "meta/info.json").write_text(json.dumps(info))
    (root / "meta/episodes.jsonl").write_text("\n".join(json.dumps({"episode_index": i, "length": 4, "tasks": ["grab"]}) for i in range(2)))
    (root / "meta/tasks.jsonl").write_text(json.dumps({"task_index": 0, "task": "grab"}))
    (root / "data/chunk-000").mkdir(parents=True)
    for ep in range(2):
        table = pa.table({
            "observation.state": pa.array([[float(ep)] * 7] * 4, pa.list_(pa.float32(), 7)),
            "action": pa.array([[0.5] * 7] * 4, pa.list_(pa.float32(), 7)),
            "timestamp": pa.array(np.arange(4) / 15, pa.float64()),
            "frame_index": pa.array(range(4), pa.int64()),
            "next.reward": pa.array([0., 0., 0., 1.], pa.float32()),
            "next.done": pa.array([False, False, False, True], pa.bool_()),
            "episode_index": pa.array([ep] * 4, pa.int64()),
            "index": pa.array(range(ep * 4, ep * 4 + 4), pa.int64()),
            "task_index": pa.array([0] * 4, pa.int64())})
        pq.write_table(table, root / f"data/chunk-000/episode_{ep:06d}.parquet")
        for cam in cameras:
            write_video(root / f"videos/chunk-000/{cam}/episode_{ep:06d}.mp4")
    return app.Source(tmp_path, app.SOURCE_REVISION, root)


def run_export(source, tmp_path, encoders=None, **kwargs):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return app.export(source, str(tmp_path / "out"), {}, work,
                      encoders or TestEncoders(), episodes_per_commit=1, **kwargs)


def test_round_trip_original_values_bytes_vectors_and_resume(source, tmp_path):
    encoders = TestEncoders()
    counts = run_export(source, tmp_path, encoders, frame_stride=2)
    assert counts == {"frames": 8, "episodes": 2, "media": 6,
                      "frame_embeddings": 12, "audio_embeddings": 0, "metadata": 3}
    assert encoders.audio_calls == 0
    frames = lance.dataset(tmp_path / "out/frames.lance").to_table()
    assert frames["task"].to_pylist() == ["grab"] * 8
    for ep in range(2):
        original = pq.read_table(source.local_root / source.relative(ep))
        for name in original.column_names:
            assert frames[app.field_name(name)].slice(ep * 4, 4).equals(original[name])
    for row in lance.dataset(tmp_path / "out/media.lance").to_table().to_pylist():
        assert row["video_bytes"] == (source.local_root / row["source_path"]).read_bytes()
        assert row["frame_count"] == 4
        assert row["embedding_frame_count"] == 2
        assert np.linalg.norm(row["video_embedding"]) == pytest.approx(1, abs=1e-6)
    vectors = lance.dataset(tmp_path / "out/frame_embeddings.lance").to_table().to_pylist()
    assert all(row["index"] == row["episode_index"] * 4 + row["frame_index"] for row in vectors)
    assert all(row["frame_index"] in (0, 2) for row in vectors)
    before = encoders.image_calls
    assert run_export(source, tmp_path, encoders, frame_stride=2, resume=True) == counts
    assert encoders.image_calls == before


def test_resume_recovers_partial_multitable_commit(source, tmp_path, monkeypatch):
    real_write = app.ExportStore.write
    fault = {"fired": False}
    def faulty_write(self, name, data, mode="append", schema=None):
        # Fail after frames commit but before media commits in the second batch.
        if name == "media" and mode == "append" and self.open("frames").count_rows() == 8:
            fault["fired"] = True
            raise RuntimeError("injected network interruption")
        return real_write(self, name, data, mode, schema)
    monkeypatch.setattr(app.ExportStore, "write", faulty_write)
    with pytest.raises(RuntimeError, match="injected"):
        run_export(source, tmp_path)
    assert fault["fired"]
    monkeypatch.setattr(app.ExportStore, "write", real_write)
    counts = run_export(source, tmp_path, resume=True)
    assert counts["frames"] == 8
    assert counts["media"] == 6
    vectors = lance.dataset(tmp_path / "out/frame_embeddings.lance").to_table()
    assert vectors.num_rows == 24
    assert len(set(vectors["sample_id"].to_pylist())) == 24


def test_reject_config_change_and_existing_output(source, tmp_path):
    run_export(source, tmp_path)
    with pytest.raises(FileExistsError):
        run_export(source, tmp_path)
    with pytest.raises(ValueError, match="different configuration"):
        run_export(source, tmp_path, resume=True, frame_stride=2)


def test_bad_video_does_not_commit_frames(source, tmp_path):
    write_video(source.local_root / source.relative(0, source.cameras[0]), count=3)
    with pytest.raises(ValueError, match="frame count"):
        run_export(source, tmp_path)
    assert lance.dataset(tmp_path / "out/frames.lance").count_rows() == 0
    write_video(source.local_root / source.relative(0, source.cameras[0]), count=4)
    assert run_export(source, tmp_path, resume=True)["frames"] == 8


def test_reject_timestamp_shift(source, tmp_path):
    path = source.local_root / source.relative(0)
    table = pq.read_table(path)
    idx = table.column_names.index("timestamp")
    table = table.set_column(idx, "timestamp", pa.array([0., .3, .4, .5]))
    pq.write_table(table, path)
    with pytest.raises(ValueError, match="timestamp mismatch"):
        run_export(source, tmp_path)


def test_audio_windows_resample_and_split(tmp_path):
    path = tmp_path / "audio.wav"
    # Decode 11 seconds of 16 kHz mono audio and validate 48 kHz windows.
    with av.open(str(path), "w") as out:
        stream = out.add_stream("pcm_s16le", rate=16000)
        stream.layout = "mono"
        for start in range(0, 176000, 1600):
            signal = (np.sin(np.arange(start, start + 1600) / 16000 * 440 * 2 * np.pi) * 10000).astype(np.int16)[None, :]
            frame = av.AudioFrame.from_ndarray(signal, format="s16", layout="mono")
            frame.sample_rate = 16000
            frame.pts = start
            frame.time_base = Fraction(1, 16000)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)
    windows = list(app.audio_windows(path, 0))
    assert len(windows) == 2
    assert windows[0][0:2] == pytest.approx((0, 10))
    assert windows[1][0:2] == pytest.approx((10, 11))
    assert len(windows[0][2]) == 480000
    assert len(windows[1][2]) == 48000


def test_actual_audio_track_produces_audio_embedding(source, tmp_path):
    path = source.local_root / source.relative(0, source.cameras[0])
    with av.open(str(path), "w") as out:
        video = out.add_stream("mpeg4", rate=15)
        video.width = video.height = 32
        video.pix_fmt = "yuv420p"
        audio = out.add_stream("aac", rate=48000)
        audio.layout = "mono"
        for i in range(4):
            frame = av.VideoFrame.from_ndarray(np.zeros((32, 32, 3), np.uint8), format="rgb24")
            frame.pts = i
            frame.time_base = Fraction(1, 15)
            for packet in video.encode(frame):
                out.mux(packet)
        for packet in video.encode():
            out.mux(packet)
        signal = np.sin(np.arange(12000) / 48000 * 440 * 2 * np.pi).astype(np.float32)[None, :]
        frame = av.AudioFrame.from_ndarray(signal, format="flt", layout="mono")
        frame.sample_rate = 48000
        frame.pts = 0
        frame.time_base = Fraction(1, 48000)
        for packet in audio.encode(frame):
            out.mux(packet)
        for packet in audio.encode():
            out.mux(packet)
    encoders = TestEncoders()
    counts = run_export(source, tmp_path, encoders)
    assert counts["audio_embeddings"] == 1
    assert encoders.audio_calls == 1
    row = lance.dataset(tmp_path / "out/audio_embeddings.lance").to_table().to_pylist()[0]
    assert row["media_id"] == f"0:{source.cameras[0]}"
    assert row["sample_rate"] == 48000
    assert row["end_s"] > row["start_s"]
    summary = app.build_indexes(str(tmp_path / "out"), {})
    assert summary["audio_embeddings"]["state"] == "ready"
    assert summary["audio_embeddings"]["rows"] == 1


def test_oss_config_and_credentials_not_serialized(monkeypatch):
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "test-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "test-secret")
    options = app.storage_options("oss://bucket/droid", "https://oss-cn-hangzhou.aliyuncs.com", "cn-hangzhou")
    assert options["oss_access_key_id"] == "test-id"
    assert options["oss_region"] == "cn-hangzhou"
    with pytest.raises(ValueError):
        app.storage_options("oss://bucket", "https://oss-cn-hangzhou.aliyuncs.com", None)


def test_reject_path_escape_and_zero_vectors():
    with pytest.raises(ValueError):
        app.safe_relative("../secret")
    with pytest.raises(ValueError):
        app.unit_vectors(np.zeros((1, 768)), 768)


def test_index_build_search_and_resume_preserves_indexes(source, tmp_path):
    encoders = TestEncoders()
    run_export(source, tmp_path, encoders)
    output = str(tmp_path / "out")
    summary = app.build_indexes(output, {})
    assert summary["audio_embeddings"]["state"] == "empty"
    assert summary["frame_embeddings"]["plan"]["index_type"] == "IVF_FLAT"
    ds = lance.dataset(output + "/frame_embeddings.lance")
    original_version = ds.version
    original_uuid = ds.list_indices()[0]["uuid"]
    query = ds.to_table(columns=["embedding"]).slice(0, 1)["embedding"][0].as_py()
    results = ds.to_table(columns=["sample_id", "camera", "_distance"],
        filter=f"camera = '{source.cameras[0]}'", prefilter=True,
        nearest={"column": "embedding", "q": query, "k": 3, "metric": "cosine", "nprobes": 1})
    assert len(results) == 3
    assert results["_distance"][0].as_py() == pytest.approx(0, abs=1e-5)
    assert set(results["camera"].to_pylist()) == {source.cameras[0]}
    before = encoders.image_calls
    run_export(source, tmp_path, encoders, resume=True)
    assert encoders.image_calls == before
    assert app.build_indexes(output, {}) == summary
    ds = lance.dataset(output + "/frame_embeddings.lance")
    assert ds.version == original_version  # Preserve the index without restoring or rebuilding it.
    assert ds.list_indices()[0]["uuid"] == original_uuid
    assert lance.dataset(output + "/export_index_status.lance").count_rows() == 1


@pytest.mark.parametrize("raise_after_commit", [False, True])
def test_index_failure_reuses_already_committed_indexes(source, tmp_path, monkeypatch, raise_after_commit):
    run_export(source, tmp_path)
    output = str(tmp_path / "out")
    original = lance.LanceDataset.create_index
    def interrupted(self, column, *args, **kwargs):
        if column == "video_embedding":
            if raise_after_commit:
                original(self, column, *args, **kwargs)
            raise RuntimeError("index connection interrupted")
        return original(self, column, *args, **kwargs)
    monkeypatch.setattr(lance.LanceDataset, "create_index", interrupted)
    with pytest.raises(RuntimeError, match="index connection"):
        app.build_indexes(output, {})
    ds = lance.dataset(output + "/frame_embeddings.lance")
    uuid = ds.list_indices()[0]["uuid"]
    assert not (tmp_path / "out/export_index_status.lance").exists()
    monkeypatch.setattr(lance.LanceDataset, "create_index", original)
    run_export(source, tmp_path, resume=True)
    summary = app.build_indexes(output, {})
    assert summary["frame_embeddings"]["uuid"] == uuid
    assert summary["media"]["state"] == "ready"


def test_completed_export_rejects_data_mutation(source, tmp_path):
    run_export(source, tmp_path)
    path = tmp_path / "out/frames.lance"
    ds = lance.dataset(path)
    lance.write_dataset(ds.to_table().slice(0, 1), path, mode="append")
    with pytest.raises(ValueError, match="fragments"):
        run_export(source, tmp_path, resume=True)
    with pytest.raises(ValueError, match="fragments"):
        app.build_indexes(str(tmp_path / "out"), {})
    assert lance.dataset(path).count_rows() == 9  # Do not overwrite external changes.


def test_index_only_cli_does_not_load_source_or_models(source, tmp_path, monkeypatch):
    run_export(source, tmp_path)
    def fail(*args, **kwargs):
        raise AssertionError("index-only must not load source data or models")
    monkeypatch.setattr(app, "Source", fail)
    monkeypatch.setattr(app, "Encoders", fail)
    monkeypatch.setattr(sys, "argv", ["export_droid.py", "--output", str(tmp_path / "out"),
                                    "--work-dir", str(tmp_path / "work"), "--index-only"])
    # Restore cache environment variables after exercising the CLI.
    for key in ["HF_HOME", "HF_HUB_DISABLE_TELEMETRY", "TMPDIR"]:
        monkeypatch.setenv(key, str(tmp_path / "work") if key != "HF_HUB_DISABLE_TELEMETRY" else "1")
    app.main()
    assert lance.dataset(tmp_path / "out/export_index_status.lance").to_table()["complete"][0].as_py()


def test_reject_changed_index_parameters(source, tmp_path):
    run_export(source, tmp_path)
    output = str(tmp_path / "out")
    app.build_indexes(output, {})
    with pytest.raises(ValueError, match="different parameters"):
        app.build_indexes(output, {}, target_partition_size=4096)


def test_indexing_requires_complete_export(source, tmp_path):
    run_export(source, tmp_path)
    path = tmp_path / "out/export_checkpoints.lance"
    last = lance.dataset(path).to_table().to_pylist()[-1]
    last["complete"] = False
    lance.write_dataset(pa.Table.from_pylist([last]), path, mode="append")
    with pytest.raises(ValueError, match="incomplete"):
        app.build_indexes(str(tmp_path / "out"), {})


def test_real_pq_index_on_seeded_vectors(tmp_path):
    """Train and query real PQ indexes on synthetic vectors, not model outputs."""
    output = tmp_path / "pq"
    vectors = app.unit_vectors(np.random.default_rng(123).normal(size=(320, 768)), 768)
    config = app.json_text({"fixture": "index_stage"})
    fingerprint = app.digest(config.encode())
    versions = {}
    for name, column, dim, size in [("frame_embeddings", "embedding", 768, 320),
                                   ("media", "video_embedding", 768, 320),
                                   ("audio_embeddings", "embedding", 512, 0)]:
        schema = pa.schema([("id", pa.int64()), (column, pa.list_(pa.float32(), dim))])
        table = pa.Table.from_pylist([{"id": i, column: vectors[i].tolist()} for i in range(size)], schema=schema)
        ds = lance.write_dataset(table, output / f"{name}.lance", data_storage_version="2.1")
        versions[name] = ds.version
    lance.write_dataset(pa.table({"config_json": [config]}), output / "export_manifest.lance")
    lance.write_dataset(pa.table({"complete": [True], "fingerprint": [fingerprint],
                                 "versions_json": [app.json_text(versions)]}),
                        output / "export_checkpoints.lance")
    summary = app.build_indexes(str(output), {})
    assert summary["frame_embeddings"]["plan"]["index_type"] == "IVF_PQ"
    ds = lance.dataset(output / "frame_embeddings.lance")
    result = ds.to_table(columns=["id", "_distance"], nearest={
        "column": "embedding", "q": vectors[77], "k": 5, "metric": "cosine",
        "nprobes": 1, "refine_factor": 5})
    assert result["id"][0].as_py() == 77
    assert result["_distance"][0].as_py() == pytest.approx(0, abs=1e-5)


@pytest.mark.parametrize("mutation,match", [({"codebase_version": "v3.0"}, "v3"),
                                          ({"state_dimension": 8}, "7-dimensional")])
def test_reject_incompatible_dataset_layout(source, tmp_path, mutation, match):
    info_path = source.local_root / "meta/info.json"
    info = json.loads(info_path.read_text())
    if "state_dimension" in mutation:
        info["features"]["observation.state"]["shape"] = [mutation["state_dimension"]]
    else:
        info.update(mutation)
    info_path.write_text(json.dumps(info))
    with pytest.raises(ValueError, match=match):
        app.Source(tmp_path, app.SOURCE_REVISION, source.local_root)


def test_query_render_uses_exported_vector_and_one_run_id(source, tmp_path):
    from doris.prepare_query import read_reference, render
    run_export(source, tmp_path)
    reference = read_reference(str(tmp_path / "out"), f"0:{source.cameras[0]}:0", {})
    output = tmp_path / "rendered"
    files = render(reference, output, "failure_001")
    assert len(files) == 4
    for path in files:
        sql = path.read_text()
        assert "{{" not in sql and "REPLACE_" not in sql
        assert "failure_001" in sql
    search = (output / "02_vector_search.sql").read_text()
    assert 'FROM vector_search(' in search
    assert "cosine_distance(" not in search
    assert "episode_index != 0" in search
    assert reference["camera"] in search
    manifest = json.loads((output / "query.json").read_text())
    assert manifest["embedding"] == reference["embedding"]
    with pytest.raises(FileExistsError):
        render(reference, output, "failure_001")


def test_query_validation_rejects_invalid_inputs(tmp_path):
    from doris.prepare_query import read_reference, render
    ref = {"embedding": app.unit_vectors(np.ones((1, 768)), 768)[0].tolist(),
           "camera": "observation.images.wrist_image_left", "episode_index": 0}
    with pytest.raises(ValueError, match="run-id"):
        render(ref, tmp_path / "invalid", "x'; DROP TABLE y;")
    with pytest.raises(ValueError, match="768 finite"):
        render({**ref, "embedding": [float("nan")] * 768}, tmp_path / "invalid", "run1")
    with pytest.raises(ValueError, match="sample_id"):
        read_reference("not-opened", "x' OR 1=1", {})
    assert not (tmp_path / "invalid").exists()


def test_extract_media_preserves_original_bytes(source, tmp_path):
    from pipeline.export_media import extract
    run_export(source, tmp_path)
    media_id = f"0:{source.cameras[0]}"
    target = tmp_path / "review/video.mp4"
    extract(str(tmp_path / "out"), media_id, target, {})
    assert target.read_bytes() == (source.local_root / source.relative(0, source.cameras[0])).read_bytes()
    with pytest.raises(FileExistsError):
        extract(str(tmp_path / "out"), media_id, target, {})


def test_source_code_and_comments_are_english():
    root = Path(__file__).resolve().parents[1]
    for directory in ("pipeline", "doris", "tests"):
        for path in (root / directory).rglob("*"):
            if path.suffix in {".py", ".sql"}:
                assert path.read_text().isascii(), str(path)


@pytest.fixture
def aws_env(monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith("AWS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "s3-test-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s3-test-secret")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def test_s3_credentials_overrides_and_provider_isolation(aws_env, monkeypatch):
    monkeypatch.setenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "oss-test-key")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "oss-test-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "temporary-token")
    options = app.storage_options("s3://bucket/droid", s3_region="eu-west-1")
    assert options["aws_region"] == "eu-west-1"
    assert options["aws_session_token"] == "temporary-token"
    assert options["aws_virtual_hosted_style_request"] == "true"
    assert not any(key.startswith("oss_") for key in options)
    assert not any(key.startswith("aws_") for key in app.storage_options("oss://bucket/droid"))
    assert app.storage_options("local/output") == {}
    monkeypatch.delenv("AWS_REGION")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    assert app.storage_options("s3://bucket/droid")["aws_region"] == "eu-central-1"
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(key)
    assert "aws_access_key_id" not in app.storage_options("s3://bucket/droid")


def test_s3_endpoint_guards_and_path_style(aws_env, monkeypatch):
    with pytest.raises(ValueError, match="AWS_ALLOW_HTTP"):
        app.storage_options("s3://bucket/droid", s3_endpoint="http://localhost:9000")
    monkeypatch.setenv("AWS_ALLOW_HTTP", "true")
    monkeypatch.setenv("AWS_ENDPOINT", "http://localhost:9000")
    options = app.storage_options("s3://bucket/droid")
    assert options["aws_endpoint"] == "http://localhost:9000"
    assert options["aws_virtual_hosted_style_request"] == "false"
    assert app.storage_options("s3://bucket/droid", s3_endpoint="https://other.example")["aws_endpoint"] == "https://other.example"
    for uri in ("s3://bucket", "s3://bucket/", "s3://bucket/prefix?secret=bad"):
        with pytest.raises(ValueError):
            app.storage_options(uri)
    for endpoint in ("https://user:password@example.com", "https://example.com/bucket", "ftp://example.com"):
        with pytest.raises(ValueError, match="HTTP"):
            app.storage_options("s3://bucket/droid", s3_endpoint=endpoint)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    with pytest.raises(ValueError, match="both"):
        app.storage_options("s3://bucket/droid")


def test_s3_protocol_export_index_resume_and_media(source, tmp_path, aws_env, monkeypatch):
    """Exercise real Lance HTTP requests against a local S3 protocol emulator."""
    import boto3
    from moto.server import ThreadedMotoServer
    from doris.prepare_query import read_reference
    from pipeline.export_media import extract

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    try:
        host, port = server.get_host_and_port()
        endpoint = f"http://{host}:{port}"
        monkeypatch.setenv("AWS_ENDPOINT", endpoint)
        monkeypatch.setenv("AWS_ALLOW_HTTP", "true")
        client = boto3.client("s3", endpoint_url=endpoint)
        client.create_bucket(Bucket="robolens-test")
        root = "s3://robolens-test/robotics/droid100"
        options = app.storage_options(root)
        work = tmp_path / "s3-work"
        work.mkdir()
        encoders = TestEncoders()
        counts = app.export(source, root, options, work, encoders, episodes_per_commit=1, frame_stride=2)
        assert counts == {"frames": 8, "episodes": 2, "media": 6,
                          "frame_embeddings": 12, "audio_embeddings": 0, "metadata": 3}
        summary = app.build_indexes(root, options)
        ds = lance.dataset(root + "/frame_embeddings.lance", storage_options=options)
        index_uuid = ds.list_indices()[0]["uuid"]
        sample_id = f"0:{source.cameras[0]}:0"
        reference = read_reference(root, sample_id, options)
        hits = ds.to_table(nearest={"column": "embedding", "q": reference["embedding"],
                                   "k": 1, "metric": "cosine", "nprobes": 1})
        assert hits["_distance"][0].as_py() == pytest.approx(0, abs=1e-5)
        calls = encoders.image_calls
        app.export(source, root, options, work, encoders, episodes_per_commit=1, frame_stride=2, resume=True)
        assert encoders.image_calls == calls
        assert app.build_indexes(root, options) == summary
        assert lance.dataset(root + "/frame_embeddings.lance", storage_options=options).list_indices()[0]["uuid"] == index_uuid
        target = tmp_path / "review.mp4"
        extract(root, f"0:{source.cameras[0]}", target, options)
        original = source.local_root / f"videos/chunk-000/{source.cameras[0]}/episode_000000.mp4"
        assert target.read_bytes() == original.read_bytes()
        manifest = lance.dataset(root + "/export_manifest.lance", storage_options=options).to_table().to_pylist()
        assert "s3-test-secret" not in json.dumps(manifest)
        assert any("/_indices/" in item["Key"] for item in client.list_objects_v2(Bucket="robolens-test")["Contents"])
    finally:
        server.stop()
