"""Explain image retrieval and display the exact source frames in notebook outputs."""
from __future__ import annotations

import base64
from html import escape
from io import BytesIO
from pathlib import Path
import re

from doris.config import Config


def load_search_frame(root: str, sample_id: str, options: dict, work: Path,
                      config: Config) -> dict:
    """Resolve a sampled vector to its original decoded video frame, not a nearby keyframe."""
    import av
    import lance
    from doris.prepare_query import SAMPLE_PATTERN
    from pipeline.export_media import extract

    if not re.fullmatch(SAMPLE_PATTERN, sample_id):
        raise ValueError("Invalid sample_id; use episode:camera:frame_index")
    vectors = lance.dataset(config.uri(root, "frame_embeddings"), storage_options=options)
    rows = vectors.to_table(filter=f"sample_id = '{sample_id}'", columns=[
        "sample_id", "episode_index", "camera", "frame_index", "timestamp", "media_id",
    ]).to_pylist()
    if len(rows) != 1:
        raise ValueError(f"Reference/hit frame {sample_id}: expected one exported vector, found {len(rows)}")
    row = rows[0]
    if f'{row["media_id"]}:{row["frame_index"]}' != sample_id:
        raise ValueError(f"Inconsistent video/frame identity for {sample_id}")
    frames = lance.dataset(config.uri(root, "frames"), storage_options=options)
    task_rows = frames.to_table(
        filter=f'episode_index = {int(row["episode_index"])} AND frame_index = {int(row["frame_index"])}',
        columns=["task"],
    ).to_pylist()
    row["task"] = task_rows[0]["task"] if len(task_rows) == 1 else ""
    video = work / (row["media_id"].replace(":", "_") + ".mp4")
    extract(root, row["media_id"], video, options, config=config)
    with av.open(str(video)) as container:
        for ordinal, frame in enumerate(container.decode(video=0)):
            if ordinal == row["frame_index"]:
                image = frame.to_image().convert("RGB")
                image.thumbnail((640, 480))
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                row["png"] = buffer.getvalue()
                return row
    raise ValueError(f"Video {row['media_id']} has no frame {row['frame_index']}")


def frame_card(frame: dict, title: str, *, similarity=None, language="zh") -> str:
    """Embed image bytes so the comparison remains visible in exported HTML."""
    zh = language == "zh"
    labels = ("帧", "源任务（仅作背景）", "余弦相似度") if zh else (
        "Frame", "Source task (context only)", "Cosine similarity")
    camera = frame["camera"].removeprefix("observation.images.")
    caption = (f'Episode {frame["episode_index"]} · {camera} · '
               f'{labels[0]} {frame["frame_index"]} · {frame["timestamp"]:.3f} s')
    score = "" if similarity is None else f"<p>{labels[2]}: <strong>{float(similarity):.4f}</strong></p>"
    task = f'<p>{labels[1]}: {escape(str(frame.get("task") or "—"))}</p>'
    data = base64.b64encode(frame["png"]).decode("ascii")
    return (f'<article style="border:1px solid #ccd6e0;border-radius:10px;padding:14px;min-width:0">'
            f'<h4 style="margin:0 0 10px">{escape(title)}</h4>'
            f'<img src="data:image/png;base64,{data}" alt="{escape(caption, quote=True)}" '
            f'style="width:100%;max-height:320px;object-fit:contain;background:#f1f5f9"/>'
            f'<p>{escape(caption)}</p>{score}{task}'
            f'<small>{escape(frame["sample_id"])}</small></article>')


def reference_html(frame: dict, config: Config, language="zh") -> str:
    zh = language == "zh"
    episode = frame["episode_index"]
    camera = escape(frame["camera"])
    if zh:
        description = (
            f"以这张参考图片为查询，寻找其他 episode 中视觉相似的画面。"
            f"检索范围是 episode 0–99 中已导出的采样帧，排除参考 episode {episode}；"
            f"仅搜索同一路相机 <code>{camera}</code>、相同 SigLIP2 模型版本的向量。"
            "按余弦距离做近似最近邻检索，先召回最多 2,000 帧，再在每个 episode 内保留最相似的一帧，"
            "最终返回最多 50 个 episode，按相似度从高到低排列。"
            "<p>这是一项以图搜图查询；源任务文字只作背景，不参与过滤。"
            "视觉相似度不是概率，也不代表动作相同、抓取失败或失败原因；这些需要查看视频并人工复核。</p>"
        )
    else:
        description = (
            f"Find visually similar frames from other episodes using this reference image. "
            f"Search exported sampled frames from episodes 0–99, excluding reference episode {episode}, "
            f"with the same camera <code>{camera}</code> and SigLIP2 model revision. "
            "Approximate cosine search retrieves up to 2,000 frames, keeps the closest frame per episode, "
            "and returns up to 50 episodes in descending similarity order. "
            "<p>This is image-to-image retrieval. Source task text is context, not a search filter. "
            "Similarity is not a probability or evidence of matching actions, grasp failure, or its cause; "
            "review the videos to make those judgments.</p>"
        )
    title = "查询参考图" if zh else "Query reference image"
    return (f'<div class="demo-note"><h3>{"这次在查什么？" if zh else "What does this search find?"}</h3>'
            f'{description}<small>{escape(config.source("frame_embeddings", quoted=False))}</small></div>'
            f'<div style="max-width:720px;margin:16px 0">{frame_card(frame, title, language=language)}</div>')


def show_search_reference(root, sample_id, options, work, config, language="zh"):
    from IPython.display import HTML, display
    frame = load_search_frame(root, sample_id, options, work, config)
    display(HTML(reference_html(frame, config, language)))
    return frame


def show_search_results(root, hits, reference, options, work, config, language="zh", limit=6):
    from IPython.display import HTML, display
    zh = language == "zh"
    if hits.empty:
        display(HTML("<p>没有命中符合条件的画面，请换一张参考图或检查检索范围。</p>" if zh else
                     "<p>No matching frames. Try another reference or check the search scope.</p>"))
        return
    selected = hits.sort_values("similarity", ascending=False).head(limit)
    cards = [frame_card(reference, "参考图（对照）" if zh else "Reference (compare)", language=language)]
    for rank, hit in enumerate(selected.to_dict("records"), 1):
        frame = load_search_frame(root, hit["sample_id"], options, work, config)
        cards.append(frame_card(frame, f'#{rank} ' + ("相似画面" if zh else "Similar frame"),
                                similarity=hit["similarity"], language=language))
    title = (f"命中画面对比：展示前 {len(selected)} / {len(hits)} 个 episode" if zh else
             f"Frame comparison: showing {len(selected)} of {len(hits)} retrieved episodes")
    display(HTML(f'<h3>{title}</h3><div style="display:grid;grid-template-columns:'
                 f'repeat(auto-fit,minmax(260px,1fr));gap:16px">{"".join(cards)}</div>'))


def show_search_sql(text, language="zh"):
    from IPython.display import HTML, display
    title = "查看检索 SQL 模板（展开）" if language == "zh" else "Inspect the search SQL template"
    display(HTML(f"<details><summary>{title}</summary><pre>{escape(text)}</pre></details>"))
