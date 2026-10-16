"""A filled, fictional review case. Never connects to or writes into Doris."""
from base64 import b64encode
from html import escape
from pathlib import Path
import sqlite3

import pandas as pd
import sqlparse
from IPython.display import HTML, display
from notebooks.presentation import table_html

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(__file__).parent / 'examples' / 'review_case'
RUN_ID = 'DEMO_REVIEW_CASE'


def load_case():
    tables = {name: pd.read_csv(DATA / f'{name}.csv', keep_default_na=False)
              for name in ('grasp_hits', 'grasp_review', 'episode_policy', 'media')}
    if set(tables['grasp_review'].run_id) != {RUN_ID}:
        raise ValueError('The example must use its own demo run ID')
    return tables


def case_results():
    """Run the existing analysis/selection SELECT templates over in-memory fixtures."""
    tables = load_case()
    substitutions = {
        'RUN_ID': RUN_ID, 'ANALYSIS_GRASP_HITS': 'grasp_hits',
        'ANALYSIS_GRASP_REVIEW': 'grasp_review',
        'ANALYSIS_EPISODE_POLICY': 'episode_policy', 'SOURCE_MEDIA': 'media',
    }
    result = {}
    with sqlite3.connect(':memory:') as connection:
        for name, frame in tables.items():
            frame.to_sql(name, connection, index=False)
        for filename, names in (
            ('04_failure_distribution.sql', ('coverage', 'distribution', 'reasons')),
            ('05_select_training_samples.sql', ('mixed_groups', 'training')),
        ):
            text = (ROOT / 'doris' / 'sql' / filename).read_text()
            for key, value in substitutions.items():
                text = text.replace('{{' + key + '}}', value)
            if '{{' in text:
                raise ValueError('Unresolved demo SQL placeholder')
            statements = [s for s in sqlparse.split(text)
                          if sqlparse.format(s, strip_comments=True).strip().strip(';')]
            if len(statements) != len(names):
                raise ValueError('Unexpected analysis template shape')
            for name, statement in zip(names, statements):
                result[name] = pd.read_sql_query(statement, connection)
    return tables, result


def _pick(language, zh, en):
    return zh if language == 'zh' else en


def _metrics(items):
    return '<div class="rl-metrics">' + ''.join(
        '<div class="rl-metric"><strong>' + escape(str(value)) +
        '</strong><span>' + escape(label) + '</span></div>' for label, value in items) + '</div>'


def _details(title, content):
    return '<details><summary>' + escape(title) + '</summary>' + content + '</details>'


def _download(name, language):
    payload = b64encode((DATA / f'{name}.csv').read_bytes()).decode('ascii')
    label = _pick(language, '下载已填好的 ', 'Download filled ')
    return (f'<a class="rl-case-download" download="demo_{name}.csv" '
            f'href="data:text/csv;base64,{payload}">{label}{name}.csv</a>')


def _decision(row, selected, language):
    p = lambda zh, en: _pick(language, zh, en)
    if not row.quality_ok:
        return p('排除：画面/动作质量不合格', 'Excluded: unusable video/action quality')
    if not row.reviewed or not row.is_target_grasp or row.outcome == 'unknown':
        return p('排除：结果未知，不能判为成功或失败', 'Excluded: outcome is unknown')
    if row.used_for_training:
        return p('排除：此前已经用于训练', 'Excluded: already used for training')
    if row.split_assignment != 'train':
        return p('排除：属于测试集，不能用于训练', 'Excluded: held-out test split')
    if row.episode_index not in selected:
        return p('去重：与 9001 同场景组、同用途，保留相似度更高的 9001',
                 'Deduplicated: same scene/use as 9001; keep its higher-scoring match')
    return p('保留：成功动作示范', 'Keep: successful-action demonstration') if row.outcome == 'success' else p('保留：失败诊断 / critic 候选', 'Keep: failure diagnosis / critic candidate')


def _fields(language, tables):
    p = lambda zh, en: _pick(language, zh, en)
    row = tables['grasp_review'].query('episode_index == 9002').iloc[0]
    policy = tables['episode_policy'].query('episode_index == 9002').iloc[0]
    descriptions = [
        ('run_id', p('本次检索批次编号；DEMO 表示独立的虚构案例', 'Search run ID; DEMO identifies this fictional case')),
        ('sample_id', p('命中图片的标识：episode 9002 + 腕部左相机 + 第 42 帧', 'Hit image: episode 9002 + left wrist camera + frame 42')),
        ('episode_index', p('这次机器人操作记录的编号；9002 不是实际数据集编号', 'Episode identifier; 9002 is fictional')),
        ('reviewed', p('true：假设标注者已经回看完整尝试', 'true: reviewer is assumed to have watched the complete attempt')),
        ('is_target_grasp', p('true：确实属于要分析的抓取任务', 'true: this is the target grasp task')),
        ('quality_ok', p('true：画面与动作数据质量可用', 'true: video and action data are usable')),
        ('object_type', p('block：抓取物体是方块', 'block: the grasped object')),
        ('scene_type', p('tabletop：操作发生在桌面场景', 'tabletop: the environment')),
        ('outcome', p('failure：这一次完整尝试失败', 'failure: this complete attempt failed')),
        ('failure_stage', p('lift：失败发生在抬起阶段', 'lift: failure occurred while lifting')),
        ('failure_reason', p('slip：物体从夹爪滑落', 'slip: the object slipped from the gripper')),
        ('clip_start_s', p('从视频开始算，第 2.0 秒开始这次尝试', 'Attempt starts 2.0 seconds into the video')),
        ('clip_end_s', p('第 4.5 秒结束；片段长 2.5 秒，不只是一张命中帧', 'Attempt ends at 4.5 seconds; a 2.5-second clip, not just one frame')),
        ('label_source', p('synthetic_demo：人工构造的演示标签，不是真实复核结论', 'synthetic_demo: constructed example, not a real human review')),
    ]
    rows = [(field, str(row[field]), meaning) for field, meaning in descriptions]
    for field, meaning in [
        ('scene_group', p('同一场景/会话分组，用于去重与防止训练测试泄漏', 'Scene/session group for deduplication and split isolation')),
        ('split_assignment', p('train：允许进入训练候选；test/validation 不允许', 'train: eligible split; test/validation must be held out')),
        ('used_for_training', p('false：此前没有用于训练', 'false: not previously used for training')),
    ]:
        rows.append((field, str(policy[field]), meaning))
    return table_html(pd.DataFrame(rows, columns=[p('字段', 'Field'), p('9002 的值', 'Value for 9002'), p('含义', 'Meaning')]), label=p('演示字段解释', 'Demo field meanings'))


def review_case_html(section='review', language='zh'):
    if language not in {'zh', 'en'} or section not in {'review', 'analysis', 'training'}:
        raise ValueError('Unsupported demo section or language')
    p = lambda zh, en: _pick(language, zh, en)
    tables, results = case_results()
    review = tables['grasp_review'].merge(tables['episode_policy'], on='episode_index')
    selected = set(results['training'].episode_index)
    badge = p('已填好的演示案例 · 虚构数据 · 无需填写或导入', 'FILLED DEMO CASE / FICTIONAL DATA / READY TO PRESENT')
    parts = ['<div class="rl-case"><span class="rl-case-badge">' + badge + '</span>']
    if section == 'review':
        parts += ['<h3>' + p('8 次抓取尝试，标注已经填好', 'Eight grasp attempts, already annotated') + '</h3>',
                  '<p>' + p('统一任务：把桌面上的方块放入容器。以下结果是为讲解构造的案例，接着在第 07、08 节自动计算统计和选样。',
                           'Task: place a tabletop block into a container. These constructed examples feed the analysis and selection shown in steps 07 and 08.') + '</p>']
        parts.append(_metrics([(p('检索命中', 'Retrieved attempts'), 8), (p('已填写复核', 'Filled reviews'), 8), (p('已登记策略', 'Filled policies'), 8)]))
        outcome_names = {'success':p('成功', 'Success'),'failure':p('失败', 'Failure'),'unknown':p('未知', 'Unknown')}
        notes = [p('已放入容器', 'Placed in container'),p('抬起时滑落', 'Slipped during lift'),p('结果被遮挡', 'Outcome occluded'),p('成功，但此前已用于训练', 'Success, but already trained on'),p('接触时未抓住；保留作测试', 'Missed grasp; held out for test'),p('记录为成功，但画面/动作质量不合格', 'Recorded success, but unusable quality'),p('与 9001 同场景、同用途的重复候选', 'Same scene/use as 9001'),p('另一个场景中抬起滑落', 'Slipped during lift in another scene')]
        compact = pd.DataFrame({p('演示 episode', 'Demo episode'):review.episode_index,
            p('结果', 'Outcome'):[outcome_names[x] for x in review.outcome],
            p('人工判断说明', 'Review explanation'):notes,
            p('片段 / 秒', 'Clip / seconds'):['2.0–4.5']*8,
            p('数据集', 'Split'):review.split_assignment})
        parts.append(table_html(compact, label=p('8 条已填好的演示标注', 'Eight filled example reviews')))
        parts.append('<div class="rl-case-focus"><strong>' + p('拿 9002 这一条讲解', 'Walk through episode 9002') + '</strong><p>' +
            p('假设视频共 8 秒。标注者回看第 2.0–4.5 秒，确认方块在抬起时滑落，因此填写 failure / lift / slip。画面可用、属于目标任务，且该场景在训练集、此前没用于训练。',
              'Assume an 8-second video. The reviewer watches 2.0–4.5 seconds and sees the block slip during lift: failure / lift / slip. Quality and task checks pass; the scene is in train and has not been used before.') + '</p></div>')
        parts.append(_details(p('每个字段是什么意思？查看 9002 的完整记录', 'What does each field mean? Full record for 9002'), _fields(language, tables)))
        parts.append(_details(p('已填好的两份 CSV（可选下载，无需编辑）', 'Filled CSV files (optional download; no editing needed)'),
                     '<p>' + p('grasp_review 保存“这次尝试怎么样”；episode_policy 保存“这段数据能否用于训练”。两个文件均已填好 8 行。',
                     'grasp_review records the attempt outcome; episode_policy records its training policy. Both contain eight filled rows.') + '</p>' +
                     _download('grasp_review', language) + _download('episode_policy', language) +
                     table_html(tables['episode_policy'], label='episode_policy.csv')))
    elif section == 'analysis':
        coverage = results['coverage'].iloc[0]
        total = int(coverage.retrieved_episodes); eligible = int(coverage.eligible_known_outcomes)
        failed = int(results['distribution'].failed_clips.sum()); successful = eligible - failed
        pct = f'{100*failed/eligible:.0f}%' if eligible else '—'
        parts += ['<h3>' + p('8 条记录 → 6 条有效 → 3 条失败', '8 records → 6 eligible → 3 failures') + '</h3>',
            _metrics([(p('已复核 / 命中', 'Reviewed / retrieved'), f'{int(coverage.reviewed_clips)}/{total}'),
                      (p('有效统计分母', 'Eligible denominator'), eligible), (p('失败 / 有效', 'Failures / eligible'), f'{failed}/{eligible}'),
                      (p('本案例失败率', 'Demo failure rate'), pct)])]
        parts.append('<div class="rl-case-flow"><div>' + p('9003：未知 → 排除', '9003: unknown → excluded') + '</div><div>' + p('9006：质量差 → 排除', '9006: poor quality → excluded') + '</div><div>' + p('其余 6 条 → 进入统计', 'Remaining six → included') + '</div></div>')
        parts.append('<div class="rl-case-bar" role="img" aria-label="' + escape(p(f'有效样本：{successful} 次成功，{failed} 次失败', f'Eligible: {successful} successes, {failed} failures')) + '"><span style="flex:' + str(successful) + '">' + p(f'成功 {successful}', f'Success {successful}') + '</span><span style="flex:' + str(failed) + '">' + p(f'失败 {failed}', f'Failure {failed}') + '</span></div>')
        parts.append('<p>' + p('50% = 3 次失败 ÷ 6 次有效尝试，不是 3 ÷ 8。9004 虽已用于训练、9005 虽属于测试集，仍可进入这次复核统计；训练筛选在下一节单独处理。',
            '50% = 3 failures / 6 eligible attempts, not 3 / 8. Already-trained 9004 and test-split 9005 still count in this review cohort; training eligibility is checked separately.') + '</p>')
        reasons=results['reasons'].copy()
        reasons.columns=[p('失败阶段', 'Failure stage'),p('原因', 'Reason'),p('次数', 'Count')]
        parts.append(table_html(reasons,label=p('失败原因分布', 'Failure reasons')))
        parts.append('<p class="rl-case-note">' + p('两次抬起滑落（9002、9008），一次接触时未抓住（9005）。这是演示样本群的统计，不代表机器人真实线上失败率。',
            'Two lift/slip failures (9002, 9008), one contact/missed_grasp failure (9005). This fictional cohort does not measure deployment performance.') + '</p>')
    else:
        parts += ['<h3>' + p('最终保留 3 条，各有不同用途', 'Three candidates, with different purposes') + '</h3>',
            _metrics([(p('成功动作正例', 'Success demonstration'), sum(results['training'].training_use=='bc_positive')),
                      (p('失败诊断候选', 'Failure diagnosis candidates'), sum(results['training'].training_use=='failure_diagnosis_or_critic'))])]
        decisions=pd.DataFrame({p('演示 episode', 'Demo episode'):review.episode_index,
            p('筛选结论与原因', 'Decision and reason'):[_decision(row,selected,language) for row in review.itertuples()]})
        parts.append(table_html(decisions,label=p('8 条案例的去向', 'Disposition of all eight cases')))
        parts.append('<div class="rl-case-focus"><strong>' + p('选出的 3 条片段', 'The three selected clips') + '</strong><p>' +
            p('9001 → 成功示范（bc_positive）；9002、9008 → 失败诊断 / critic 候选。每条截取 2.0–4.5 秒。失败动作不能直接当成正确动作去模仿；这里也没有实际启动模型训练。',
              '9001 → bc_positive; 9002 and 9008 → failure_diagnosis_or_critic. Each clip spans 2.0–4.5 seconds. Failed actions are not correct-action targets; no model training is started.') + '</p></div>')
        parts.append(_details(p('查看完整训练候选记录', 'Inspect complete training candidate records'),table_html(results['training'],label='Demo training candidates')))
    parts.append('<p class="rl-case-note">' + p('编号 9001–9008、标注、相似度均为虚构示例。使用项目现有统计与选样 SQL 在独立内存数据上计算；未写入 Doris，也不替代真实检索结果。',
        'IDs 9001–9008, labels and scores are fictional. Existing analysis/selection SQL runs on isolated in-memory data. Nothing is written to Doris or substituted for live search results.') + '</p></div>')
    return ''.join(parts)


def show_review_case(section='review', language='zh'):
    display(HTML(review_case_html(section, language)))


def show_label_import_status(imported, language='zh'):
    """Empty optional live imports must not look like the filled case failed."""
    if not any(imported.values()):
        message = _pick(language,
            '真实标签文件目前为空，本次未写入 Doris。上方 8 条已填好的演示案例可直接讲解，第 07、08 节已有对应统计和选样结果。',
            'Live label files are empty; nothing was written to Doris. The filled eight-record demo above is ready, with analysis and selection in steps 07 and 08.')
        display(HTML('<p class="rl-case-note">' + message + '</p>'))
    else:
        frame = pd.DataFrame(imported.items(), columns=['Table', 'Imported rows'])
        display(HTML(table_html(frame, label='Live label import')))
