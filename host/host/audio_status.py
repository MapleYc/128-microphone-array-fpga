"""PCM receive-level telemetry only; samples are never modified."""
import math, struct, threading, time

class PcmLevel:
    """Track a 200 ms level window; exclude DC only from the displayed RMS."""
    def __init__(self):
        self.lock=threading.Lock();self.packets=0;self.samples=0;self.last_received=None
        self.n=0;self.total=0;self.squares=0;self.low=32767;self.high=-32768;self.level=None
    @staticmethod
    def measure(n,total,squares,low,high):
        if not n:return dict(dc=None,ac_rms=None,ac_dbfs=None,peak=None)
        mean=total/n;rms=math.sqrt(max(0,squares/n-mean*mean))
        return dict(dc=mean,ac_rms=rms,ac_dbfs=20*math.log10(rms/32768) if rms else None,peak=max(abs(low),abs(high)))
    def feed(self,pcm,now=None):
        if len(pcm)%2:raise ValueError('PCM16 byte count must be even')
        if not pcm:return
        values=struct.unpack('<%dh'%(len(pcm)//2),pcm)
        with self.lock:
            self.packets+=1;self.samples+=len(values);self.last_received=time.monotonic() if now is None else now
            self.n+=len(values);self.total+=sum(values);self.squares+=sum(x*x for x in values)
            self.low=min(self.low,min(values));self.high=max(self.high,max(values))
            if self.n>=3200:
                self.level=self.measure(self.n,self.total,self.squares,self.low,self.high)
                self.n=0;self.total=0;self.squares=0;self.low=32767;self.high=-32768
    def snapshot(self,now=None):
        now=time.monotonic() if now is None else now
        with self.lock:
            level=(self.level or self.measure(self.n,self.total,self.squares,self.low,self.high)).copy()
            return dict(level,packets=self.packets,samples=self.samples,
                age_seconds=None if self.last_received is None else max(0,now-self.last_received))

def _number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def level_text(level):
    if not isinstance(level,dict) or not _number(level.get('packets')):return '状态未知'
    if not level['packets']:return '未收到'
    age=level.get('age_seconds')
    if not _number(age) or age<0:return '更新时间未知'
    if age>1:return '接收已中断'
    db=level.get('ac_dbfs')
    return '无交流变化' if db is None else '%.1f dBFS'%db if _number(db) else '电平未知'


def playback_text(status):
    from player_metrics import normalize_player
    status=status if isinstance(status,dict) else {}
    mode=status.get('mode');names={0:'静音；点击按钮选择监听',2:'单麦参考',3:'原板端定向拾音',4:'PC128三维定向增强48k'}
    raw=status.get('raw',{});beam=status.get('beam',{})
    player=normalize_player(status.get('player'),rate_hint=48000 if mode==4 else 16000)
    route=status.get('audio_route') or {}
    result='监听：'+names.get(mode,'未知音源')+' · 接收到单麦 '+level_text(raw)+' / 定向 '+level_text(beam)
    gain=status.get('gain') or {};board=status.get('board_processing') or {}
    if _number(gain.get('gain_db')):result+=' · 监听增益 +%.0f dB'%gain['gain_db']
    shift=board.get('gain_shift')
    if board and not board.get('enabled'):result+=' · 板处理旁路'
    elif board and type(shift) is int and 0<=shift<=31:result+=' · 板处理 ×%d / 去直流'%(1<<shift)
    else:result+=' · 板处理状态未确认'
    error=route.get('error') or (player or {}).get('error')
    if error:result+=' · 播放错误 '+str(error)
    elif mode:
        selected=status.get('output',beam) if mode==4 else raw if mode==2 else beam
        selected=selected if isinstance(selected,dict) else {}
        age=selected.get('age_seconds')
        if not selected.get('packets') or not _number(age) or age>1:result+=' · 等待所选音频'
        elif player:
            completed=player.get('completed_samples');completion_age=player.get('completion_age_seconds')
            if _number(completed) and completed>0 and _number(completion_age) and 0<=completion_age<1:
                result+=' · 声卡完成计数近期更新'
            elif _number(completed) and completed>0:result+=' · 声卡已报告完成 %d 样本；完成时刻未知'%completed
            elif _number(player.get('submitted_samples')) and player['submitted_samples']>0:
                result+=' · 已提交声卡，等待完成回执'
            else:result+=' · 等待系统声卡'
        else:result+=' · 等待系统声卡'
    if mode:
        output=status.get('output') or {}
        if output:result+=' · 输出 '+level_text(output)
        reduction=gain.get('limiter_reduction_db')
        if _number(reduction) and reduction>1:result+=' · 监听限幅 %.1f dB'%reduction
        age=output.get('age_seconds');db=output.get('ac_dbfs')
        if output.get('packets') and _number(age) and 0<=age<=1 and (db is None or _number(db) and db< -55):
            result+=' · 电平偏小，请提高监听增益'
    if player:
        result+=' · 播放状态格式 '+str(player.get('backend_schema','unknown'))
        if player.get('completion_age_source')=='counter-observation':result+='；完成年龄来自计数观察，非设备事件时间'
    return result
