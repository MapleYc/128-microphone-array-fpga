"""Presentation of board decisions; never infer or change lock state on PC."""
def guard_caption(guard):
    if not guard:return ''
    if guard.get('keyword_id')==3:
        if not guard.get('voice_active') or guard.get('voice_ms',0)<80:
            return '无明显语音 · NO 已拦截'
        if guard.get('score_q15',0)<guard.get('no_threshold_q15',32768):
            return 'NO 未达解锁门槛'
        return 'NO 候选 · 以板端解锁回执为准'
    return '模型原始得分 · 板端判定口令'

def release_caption(guard):
    if not guard or guard.get('last_action')!=2:return ''
    return {1:'NO 口令解锁',2:'采样重置解锁',3:'锁定超时',4:'手动解锁',5:'识别开关重置解锁'}.get(guard.get('last_reason'),'')

def matching_guard(guard,status,age):
    if (age>1 or guard.get('epoch')!=status.get('epoch') or
        guard.get('inferences')!=status.get('inferences')):return {}
    return guard
