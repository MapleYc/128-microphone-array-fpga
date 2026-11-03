"""Select a current measured DOA for camera alignment; never use lock as a measurement."""
import math


def capture_metadata(frame,history,current_epoch,now,last_status_at):
    if frame is None:raise ValueError('尚未收到视频帧，请先恢复实时画面')
    if frame.width!=1280 or frame.height!=720:raise ValueError('校准需要原始1280×720视频帧')
    if not 0<=now-frame.received_at<=.3:raise ValueError('视频帧已过期，请等待实时画面')
    if not 0<=now-last_status_at<=.3:raise ValueError('方向状态已过期，请恢复串口状态')
    candidates=[]
    for status in history:
        quality=status.get('direction_quality',0);age=status.get('direction_age_ms',float('inf'))
        angles=(status.get('azimuth'),status.get('elevation'))
        if status.get('epoch')!=current_epoch:continue
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (quality,age,*angles)):continue
        if quality<.5 or not 0<=age<=200:continue
        if not -80<angles[0]<80 or not -60<angles[1]<60:continue
        doa_frame=status['source_frame']-round(age*48)
        delta=abs(doa_frame-frame.source_frame)
        if delta<=9600:candidates.append((delta,status,doa_frame))
    if not candidates:
        same_epoch=[s for s in history if s.get('epoch')==current_epoch]
        if not same_epoch:raise ValueError('方向与当前运行版本不一致，请等待新的方向状态')
        latest=max(same_epoch,key=lambda s:s.get('source_frame',-1))
        quality=latest.get('direction_quality');age=latest.get('direction_age_ms')
        if not isinstance(quality,(int,float)) or not math.isfinite(quality):
            raise ValueError('方向质量字段无效，请等待新的方向状态')
        if not isinstance(age,(int,float)) or not math.isfinite(age) or age<0:
            raise ValueError('方向时间字段无效，请等待新的方向状态')
        if quality<.5:
            raise ValueError(f'方向质量不足：{quality:.0%}（需≥50%），方向年龄 {age:.0f}ms；保持单声源持续说话')
        if age>200:
            raise ValueError(f'最近方向已过期：{age:.0f}ms（需≤200ms），质量 {quality:.0%}；请持续发声等待新定位，SEARCH也可校准')
        raise ValueError(f'画面与实际方向测量未在200ms内匹配，质量 {quality:.0%} / 年龄 {age:.0f}ms；保持声源静止持续说话')
    delta,status,doa_frame=min(candidates,key=lambda item:item[0])
    return dict(azimuth=status['azimuth'],elevation=status['elevation'],
                direction_quality=status['direction_quality'],epoch=status['epoch'],
                direction_age_ms=status['direction_age_ms'],
                doa_source_frame=doa_frame,video_frame_id=frame.frame_id,
                video_source_frame=frame.source_frame,source_frame=status['source_frame'],
                time_match_samples=delta,measurement_mode=status.get('mode'))
