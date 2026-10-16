"""Check the filled walkthrough against the production SELECT templates."""
import csv
from notebooks.review_case import DATA, ROOT, RUN_ID, case_results, review_case_html


def test_filled_case_matches_import_schemas_and_join_keys():
    tables, _ = case_results()
    for name in ('grasp_review', 'episode_policy'):
        with (DATA / f'{name}.csv').open() as f:
            header = next(csv.reader(f))
        assert header == (ROOT / 'doris/templates' / f'{name}.csv').read_text().strip().split(',')
        assert len(tables[name]) == 8
    assert set(tables['grasp_review'].run_id) == {RUN_ID}
    assert set(tables['grasp_hits'].sample_id) == set(tables['grasp_review'].sample_id)
    assert set(tables['grasp_review'].episode_index) == set(tables['episode_policy'].episode_index)
    assert set(tables['grasp_hits'].media_id) == set(tables['media'].media_id)


def test_demo_denominator_and_failure_breakdown_use_real_sql():
    _, results = case_results()
    assert results['coverage'].iloc[0].to_dict() == {
        'retrieved_episodes': 8, 'reviewed_clips': 8, 'eligible_known_outcomes': 6}
    group = results['distribution'].iloc[0]
    assert (group.reviewed_known_clips, group.failed_clips, group.failure_pct) == (6, 3, 50)
    reasons = {(r.failure_stage, r.failure_reason): r.failed_clips for r in results['reasons'].itertuples()}
    assert reasons == {('lift', 'slip'): 2, ('contact', 'missed_grasp'): 1}


def test_demo_selection_excludes_holdout_used_unknown_bad_quality_and_duplicate():
    _, results = case_results()
    assert results['mixed_groups'].empty
    actual = dict(zip(results['training'].episode_index, results['training'].training_use))
    assert actual == {9001: 'bc_positive', 9002: 'failure_diagnosis_or_critic', 9008: 'failure_diagnosis_or_critic'}
    for row in results['training'].itertuples():
        assert (row.clip_start_s, row.clip_end_s) == (2.0, 4.5)


def test_case_is_visible_and_identified_in_both_languages():
    for language, marker in [('zh', '\u865a\u6784'), ('en', 'FICTIONAL')]:
        for section in ('review', 'analysis', 'training'):
            html = review_case_html(section, language)
            assert marker in html
            assert '<table' in html
            assert '9002' in html
        assert 'download="demo_grasp_review.csv"' in review_case_html('review', language)
        assert '50%' in review_case_html('analysis', language)
