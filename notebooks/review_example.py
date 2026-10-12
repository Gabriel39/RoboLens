"""Self-contained, non-persistent annotation walkthrough for live and offline demos."""
from html import escape
import json
from pathlib import Path

LABELS = {
    'zh': {
        'badge':'交互示例 · 虚构数据', 'title':'看完一次尝试，如何填写标注？',
        'intro':'试着切换结果和训练登记，看它们怎样影响统计与选样。这里的画面、编号和判断都是演示样例。',
        'outcomeTitle':'01 / 复核结果', 'success':'成功', 'failure':'失败', 'unknown':'未知',
        'object':'物体类型', 'scene':'场景类型', 'stage':'失败阶段', 'reason':'失败原因',
        'lift':'抬起 · lift', 'contact':'接触 · contact', 'transport':'搬运 · transport',
        'slip':'滑落 · slip', 'missed':'未抓住 · missed_grasp', 'collision':'碰撞 · collision',
        'clipTitle':'02 / 片段与质量', 'clipHint':'假设视频共 8 秒。填写完整尝试的起止时间，而不是只标命中帧。',
        'start':'开始 / 秒', 'end':'结束 / 秒', 'reviewed':'已回看完整尝试', 'target':'属于目标抓取任务', 'quality':'画面和动作质量可用',
        'policyTitle':'03 / 训练登记', 'group':'场景组', 'split':'数据集划分', 'train':'训练集 · train',
        'validation':'验证集 · validation', 'test':'测试集 · test', 'used':'此前已经用于训练',
        'groupHint':'本例假设同一场景组没有跨集合。真实登记必须覆盖全部 episode，并检查同组划分。',
        'stats':'对第 07 节的影响', 'training':'对第 08 节的影响',
        'statsYes':'符合有效统计样本的条件。此处只演示规则，不计入真实统计。',
        'statsNo':'不进入有效统计分母：检查复核、任务、质量、成败及时间。',
        'bc':'成功正例候选 · bc_positive。后续还需场景去重和配额筛选。',
        'critic':'失败诊断 / critic 候选。不会把失败动作当成正确动作示范。',
        'trainNo':'暂不进入训练候选：检查片段有效性、复核结果、数据集划分和训练历史。',
        'generate':'预览示例记录', 'reset':'恢复示例', 'localOnly':'仅更新本页面，不保存或提交',
        'records':'展开两份示例 CSV', 'csvHint':'DEMO_ONLY_DO_NOT_IMPORT 表示演示记录。真实标注必须使用当前检索的 run_id、sample_id 和 episode_index；policy 文件还需覆盖全部已导出集。',
        'footer':'画面是流程示意，没有自动推断真实视频的成败。本面板不修改标签文件、不连接 Doris，也不影响第 07–08 节的真实结果。',
        'invalid':'请填写有效时间：0 ≤ 开始 < 结束 ≤ 8 秒。无效时不会生成记录。',
        'scenario':'虚构任务：把方块放入容器', 'approach':'接近目标', 'inspect':'回看动作过程',
        'placed':'放入容器', 'failed':'任务未完成', 'occluded':'结果无法确认', 'illustration':'流程示意 · 非真实视频帧',
    },
    'en': {
        'badge':'INTERACTIVE EXAMPLE / FICTIONAL DATA', 'title':'How do you annotate an attempt?',
        'intro':'Change the outcome and training policy to see how review affects analysis and selection. Images, IDs and judgments here are fictional.',
        'outcomeTitle':'01 / Review outcome', 'success':'Success', 'failure':'Failure', 'unknown':'Unknown',
        'object':'Object type', 'scene':'Scene type', 'stage':'Failure stage', 'reason':'Failure reason',
        'lift':'Lift', 'contact':'Contact', 'transport':'Transport', 'slip':'Slip', 'missed':'Missed grasp', 'collision':'Collision',
        'clipTitle':'02 / Clip and quality', 'clipHint':'Assume an 8-second video. Mark a complete attempt rather than only the retrieved frame.',
        'start':'Start / seconds', 'end':'End / seconds', 'reviewed':'Complete attempt reviewed', 'target':'Matches the target grasp task', 'quality':'Usable video and action data',
        'policyTitle':'03 / Training policy', 'group':'Scene group', 'split':'Dataset split', 'train':'Train', 'validation':'Validation', 'test':'Test',
        'used':'Previously used for training', 'groupHint':'This example assumes no mixed splits in the scene group. Real policy must cover every episode and check group membership.',
        'stats':'Effect on step 07', 'training':'Effect on step 08',
        'statsYes':'Meets the statistical denominator criteria. This only illustrates rules; no real statistics change.',
        'statsNo':'Excluded from the denominator: check review, task, quality, known outcome and clip bounds.',
        'bc':'Success candidate / bc_positive. Scene deduplication and quotas still apply.',
        'critic':'Failure diagnosis / critic candidate, not a correct-action demonstration.',
        'trainNo':'Not currently eligible: check clip bounds, review, split and training history.',
        'generate':'Preview example records', 'reset':'Reset example', 'localOnly':'Page preview only / no saving or submission',
        'records':'Inspect both example CSV records', 'csvHint':'DEMO_ONLY_DO_NOT_IMPORT marks demo records. Real labels must use the current run_id, sample_id and episode_index; policy must cover every exported episode.',
        'footer':'The storyboard is illustrative. It does not infer real video outcomes. This panel never changes label files, connects to Doris or affects real analysis and selection.',
        'invalid':'Use valid bounds: 0 <= start < end <= 8 seconds. Invalid inputs do not produce records.',
        'scenario':'Fictional task: put the block into the container', 'approach':'Approach', 'inspect':'Review the attempt',
        'placed':'Placed in container', 'failed':'Task incomplete', 'occluded':'Outcome unclear', 'illustration':'Illustration / not a real video frame',
    },
}


def example_document(language='zh'):
    if language not in LABELS:
        raise ValueError('Unsupported example language')
    labels = LABELS[language]
    text = (Path(__file__).parent / 'assets/review_example.html').read_text()
    return (text.replace('{{LANG}}', 'zh-CN' if language == 'zh' else 'en')
            .replace('{{TITLE}}', escape(labels['title']))
            .replace('{{STORY_LABEL}}', escape(labels['scenario'], quote=True))
            .replace('{{LABELS_JSON}}', json.dumps(labels, ensure_ascii=False).replace('<', '\\u003c')))


def annotation_example_html(language='zh'):
    title = escape(LABELS[language]['title'], quote=True)
    document = escape(example_document(language), quote=True)
    return (f'<iframe class="rl-annotation-example" title="{title}" sandbox="allow-scripts" '
            f'srcdoc="{document}" style="width:100%;height:1100px;border:1px solid #dce5ed;'
            'border-radius:18px;background:#f3f7fb" loading="lazy"></iframe>')


def show_annotation_example(language='zh'):
    from IPython.display import HTML, display
    display(HTML(annotation_example_html(language)))
