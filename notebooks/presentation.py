"""Compact presentation outputs; keep detailed evidence available on demand."""
from html import escape
from IPython.display import HTML, display


def show_details(title, *values, language='zh'):
    translations = {"Connection configuration": "连接配置", "Source scale": "源数据规模", "Export counts": "导出行数",
                    "Embedding schema": "向量表结构", "Sample rows": "示例数据行", "Index details": "索引详情",
                    "Retrieved rows": "完整检索结果", "Video and action records": "视频与动作明细",
                    "Analysis rows": "统计明细", "Excluded scene groups": "被排除的场景组"}
    if language == 'zh':
        title = translations.get(title, title)
    parts = []
    for value in values:
        if hasattr(value, 'to_html'):
            parts.append(value.to_html(index=False, escape=True))
        else:
            parts.append('<pre>' + escape(str(value)) + '</pre>')
    display(HTML('<details><summary>' + escape(title) + '</summary>' + ''.join(parts) + '</details>'))


def show_metrics(items, language='zh'):
    labels = {"Episodes": "源数据集数", "Cameras": "相机视角", "State / action steps": "状态 / 动作时间步",
              "Retrieved": "命中集数", "Reviewed": "已复核片段", "Eligible outcomes": "有效统计样本"}
    if language == 'zh':
        items = [(labels.get(label, label), value) for label, value in items]
    cards = ''.join('<div class="rl-metric"><strong>' + escape(str(value)) +
                    '</strong><span>' + escape(label) + '</span></div>' for label, value in items)
    display(HTML('<div class="rl-metrics">' + cards + '</div>'))


def show_mode(mode, language='zh'):
    labels = {'zh': {'preview': '流程讲解 · 未执行真实查询', 'live': '真实环境 · 结果来自本次运行'},
              'en': {'preview': 'Workflow preview / no live queries', 'live': 'Live environment / current run'}}
    display(HTML(f'<div class="rl-mode">{escape(mode.upper())} · {labels[language][mode]}</div>'))
