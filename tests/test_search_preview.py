"""Verify preview pixels come from the requested original frame and travel with HTML."""
import base64
from io import BytesIO
import re

import numpy as np
from PIL import Image
import pytest

from doris.config import Config
from notebooks.search_preview import load_search_frame, frame_card, reference_html, show_search_results
from test_export import source, run_export


def test_search_preview_decodes_requested_frame_and_preserves_context(source, tmp_path):
    run_export(source, tmp_path, frame_stride=2)
    config = Config.from_env({})
    sample = f'0:{source.cameras[0]}:2'
    frame = load_search_frame(str(tmp_path / 'out'), sample, {}, tmp_path / 'media', config)
    pixels = np.asarray(Image.open(BytesIO(frame['png'])))
    # Test video has distinct solid colors by frame: frame 0 is 20, frame 2 is 100.
    assert pixels.mean() == pytest.approx(100, abs=5)
    assert frame['frame_index'] == 2 and frame['timestamp'] == pytest.approx(2 / 15)
    assert frame['task'] == 'grab' and frame['sample_id'] == sample
    assert load_search_frame(str(tmp_path / 'out'), sample, {}, tmp_path / 'media', config)['png'] == frame['png']
    html = frame_card({**frame, 'task': '<script>alert(1)</script>'}, 'Reference', similarity=0.875)
    assert '<script>' not in html and '&lt;script&gt;' in html and '0.8750' in html
    data = re.search('base64,([^" ]+)', html).group(1)
    assert base64.b64decode(data) == frame['png']
    assert config.source('frame_embeddings', quoted=False) in reference_html(frame, config)
    assert 'context, not a search filter' in reference_html(frame, config, language='en')
    with pytest.raises(ValueError, match='found 0'):
        load_search_frame(str(tmp_path / 'out'), f'0:{source.cameras[0]}:1', {}, tmp_path / 'media', config)


def test_search_gallery_empty_and_ranked_limit(monkeypatch):
    import pandas as pd
    import IPython.display
    import notebooks.search_preview as preview
    outputs, samples = [], []
    monkeypatch.setattr(IPython.display, 'display', lambda item: outputs.append(item.data))
    image = Image.new('RGB', (2, 2), 'red')
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    reference = dict(sample_id='0:observation.images.wrist_image_left:0',
                     episode_index=0, camera='observation.images.wrist_image_left',
                     frame_index=0, timestamp=0.0, task='grab', png=buffer.getvalue())
    def load(root, sample_id, options, work, config):
        samples.append(sample_id)
        return {**reference, 'sample_id': sample_id}
    monkeypatch.setattr(preview, 'load_search_frame', load)
    show_search_results('', pd.DataFrame(), reference, {}, None, None, language='en')
    assert not samples and 'No matching frames' in outputs[-1]
    hits = pd.DataFrame([{'sample_id': 'low', 'similarity': .2},
                         {'sample_id': 'high', 'similarity': .9}])
    show_search_results('', hits, reference, {}, None, None, limit=1)
    assert samples == ['high']
    assert outputs[-1].count('data:image/png;base64,') == 2  # Reference plus best hit.
    assert '0.9000' in outputs[-1] and '1 / 2' in outputs[-1]
