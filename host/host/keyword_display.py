"""Filter presentation only; never change board decisions or recorded scores."""
import math

def threshold_percent(value):
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('关键词显示门槛须为 0～100%')
    return value

def keyword_presentation(status, minimum=85, fresh=True):
    minimum = threshold_percent(minimum)
    if not fresh or not status:
        return '—', '等待板端识别结果'
    score = status.get('keyword_score', 0)
    word = status.get('keyword', 'unknown')
    if not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
        return '未知', '板端得分无效'
    if score * 100 < minimum:
        return '未知', f'得分 {score:.1%} · 低于显示门槛 {minimum:g}%'
    name = {'silence': '静音', 'unknown': '未知', 'yes': 'YES', 'no': 'NO'}.get(word, '未知')
    return f'{name} {score:.0%}', f'显示门槛 {minimum:g}% · 口令动作以板端回执为准'
