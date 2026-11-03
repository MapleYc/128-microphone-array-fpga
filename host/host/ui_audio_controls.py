"""UI-only audio choices; implementation details remain in diagnostics."""
MAIN_CHOICES=((2,'单麦参考'),(4,'定向增强'),(0,'静音'))
DEBUG_CHOICES=((3,'旧板端定向16k（调试）'),)
DEBUG_COMMANDS=(('filter_on','旧抑制开',b'n'),('filter_off','旧抑制关 / 旁路',b'o'),
                ('gain_on','旧增益开',b'g'),('gain_bypass','旧增益旁路',b'b'))

def build_audio_choices(parent,listen,button_factory,choices=MAIN_CHOICES):
    controls={}
    for mode,name in choices:
        b=button_factory(parent,name,lambda m=mode:listen(m))
        b.pack(side='left',padx=1)
        controls[mode]=b
    return controls

def build_debug_commands(parent,command,reference,button_factory):
    controls={}
    for key,name,payload in DEBUG_COMMANDS:
        b=button_factory(parent,name,lambda c=payload:command(c))
        b.pack(side='left',padx=2);controls[key]=b
    b=button_factory(parent,'旧参考 B 设置',reference)
    b.pack(side='left',padx=2);controls['reference']=b
    return controls

def gain_control_state(audio):
    """Playback gain controls follow the actual selected-source capability.

    A missing PCM48 setter capability stays disabled. Requested and native
    applied values remain distinct; an unopened device is not an application ACK.
    """
    import math
    mode=audio.get('mode',0);gain=audio.get('gain') or {};pc_selected=mode==4
    ceiling=18. if pc_selected else 48.
    enabled=bool(gain.get('controllable',not pc_selected))
    try:
        minimum=float(gain.get('min_db',0.));maximum=float(gain.get('max_db',ceiling))
        value=float(gain.get('requested_gain_db',gain.get('gain_db',0.)))
        if not all(math.isfinite(x) for x in (minimum,maximum,value)) or not 0<=minimum<=maximum<=ceiling:
            raise ValueError('Invalid gain control range')
        if not minimum<=value<=maximum:raise ValueError('Requested gain outside actual range')
    except (TypeError,ValueError):
        minimum,maximum,value,enabled=0.,ceiling,0.,False
    applied=gain.get('applied_gain_db')
    try:
        applied=float(applied) if applied is not None else None
        if applied is not None and not math.isfinite(applied):applied=None
    except (TypeError,ValueError):applied=None
    pending=bool(pc_selected and (gain.get('pending',False) or applied is None or not math.isclose(value,applied,abs_tol=.001)))
    caption=f'监听增益 +{value:.0f} dB'
    if pc_selected and not enabled:caption+='（不可用）'
    elif pending:caption+='（待应用）'
    return dict(enabled=enabled,min_db=minimum,max_db=maximum,value=value,
                applied_gain_db=applied,pending=pending,caption=caption)

def playback_caption(audio):
    """Describe playback/readiness, never infer acoustic success from readiness."""
    from player_metrics import normalize_player
    mode=audio.get('mode',0)
    label={2:'单麦参考',3:'调试音源',4:'定向增强'}.get(mode,'音频')
    route=audio.get('audio_route') or {}
    player=normalize_player(audio.get('player'),rate_hint=48000 if mode==4 else 16000) or {}
    if route.get('error'):return label+' · 播放通路异常，请查看诊断'
    if player.get('error'):return label+' · 播放异常，请查看诊断'
    if mode==0:return '已静音'
    completed=bool(player.get('completed_samples'))
    age=player.get('completion_age_seconds')
    if mode!=4:
        return label+(' · 正在播放' if completed and age is not None and age<1 else ' · 已收到播放完成记录' if completed else ' · 等待音频')
    pc=audio.get('pc_state') or {}
    state=pc.get('state','')
    if pc.get('error') or pc.get('sticky_failure'):
        return label+' · 增强异常，请查看诊断'
    if state=='unsupported_nonzero_elevation':return label+' · 当前俯仰角暂不支持'
    if state in ('awaiting_board_lock','board_search_unlocked','audio_pc_selected_waiting_fresh_status'):
        return label+' · 等待目标锁定'
    if state=='board_status_timeout':return label+' · 锁定状态已过期'
    if state.startswith(('raw_','uart_','controller_')) or state in ('dsp_inbox_failure','dsp_backend_failed','bridge_failed'):
        return label+' · 音频中断，请查看诊断'
    if state=='dsp_warming':return label+' · 增强准备中'
    if completed and age is not None and age<1:return label+' · 正在播放'
    return label+(' · 已收到播放完成记录' if completed else ' · 等待增强音频')
