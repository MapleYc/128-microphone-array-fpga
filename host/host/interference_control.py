"""Settings transport only; no signal processing or inferred interference angle."""
import math

def reference_supported(report,age,epoch):
    """A framed B command must never reach a legacy single-byte interpreter."""
    return (report.get('version')==3 and report.get('hardware_id')==0x574e0100 and
            epoch is not None and report.get('epoch')==epoch and
            isinstance(age,(int,float)) and math.isfinite(age) and 0<=age<=1.5)

def reference_command(azimuth,elevation,strength_shift):
    az=int(azimuth);el=int(elevation);strength=int(strength_shift)
    if not -80<=az<=80 or not -40<=el<=40 or not 0<=strength<=4:
        raise ValueError('方位需在 -80～80°，俯仰 -40～40°，强度档位 0～4')
    body=f'B{az:+04d}{el:+04d}{strength}'.encode('ascii')
    checksum=0
    for value in body:checksum^=value
    return b'@'+body+f'*{checksum:02X}\n'.encode('ascii')

def filter_caption(df):
    if df.get('faults'):return f"板端音频链路异常 0x{df['faults']:x} · 查看诊断，当前效果未确认"
    if df.get('clipped'):return f"板端滤波曾出现 {df['clipped']} 次饱和 · 请降低输入幅度后复测"
    if not df.get('enabled'):return '板端抑制已关闭 · 双波束 A 同增益对照'
    if df.get('reference_pending'):return '干扰方向正在写入板端 · 等待提交回执'
    if not df.get('reference_valid'):return '先设置干扰方向 B · 当前只输出目标波束 A'
    b=f"B {df['reference_azimuth']:+.0f}° / {df['reference_elevation']:+.0f}°"
    if not df.get('locked'):return f'{b} 已设置 · 锁定目标后生效'
    if df.get('target_protected'):return f'{b} 距目标太近 · 保护目标，暂停抑制'
    if not df.get('primed'):return f'{b} · 等待同步双波束'
    if not df.get('active'):return f'{b} · 等待板端使能'
    return f"板端频域抑制运行中 · {b} · 强度 ×{1<<df['strength_shift']}"
