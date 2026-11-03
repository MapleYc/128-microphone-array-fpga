"""Board-angle presentation and empirically checked camera alignment only.

No microphone samples, keyword model or steering decisions are processed here.
Quick alignment is an angular homography over the measured field, not a lens
intrinsics calibration or a measurement of acoustic accuracy.
"""
from functools import lru_cache
import math
from pathlib import Path

MODEL='measured_angular_homography_v1'


def ray_xy(azimuth,elevation):
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (azimuth,elevation)):
        raise ValueError('方向包含无效数值')
    if not -80<azimuth<80 or not -60<elevation<60:raise ValueError('方向超出正面标定范围')
    a,e=map(math.radians,(azimuth,elevation))
    return math.tan(a),-math.tan(e)/math.cos(a)


def hull(points):
    pts=sorted(set(tuple(p) for p in points))
    def cross(o,a,b):return (a[0]-o[0])*(b[1]-o[1])-(a[1]-o[1])*(b[0]-o[0])
    lower=[];upper=[]
    for group,out in ((pts,lower),(list(reversed(pts)),upper)):
        for p in group:
            while len(out)>=2 and cross(out[-2],out[-1],p)<=0:out.pop()
            out.append(p)
    return lower[:-1]+upper[:-1]


def inside(point,polygon):
    if len(polygon)<3:return False
    x,y=point
    return all((b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0])>=-1e-8
               for a,b in zip(polygon,polygon[1:]+polygon[:1]))


def _project_h(h,xy):
    v=[sum(row[i]*xy[i] for i in range(2))+row[2] for row in h]
    if not all(math.isfinite(x) for x in v) or abs(v[2])<1e-9:return None
    return v[0]/v[2],v[1]/v[2]


def fit_alignment(points):
    """Fit six separate training positions; four later points are held out."""
    import numpy as np
    if len(points)!=10:raise ValueError('需要 6 个拟合点和 4 个独立验证点')
    if [p['split'] for p in points]!=['train']*6+['validation']*4:raise ValueError('拟合/验证顺序不正确')
    xy=np.array([ray_xy(p['azimuth'],p['elevation']) for p in points])
    uv=np.array([[p['pixel_x'],p['pixel_y']] for p in points],float)
    if not np.isfinite(uv).all() or np.any(uv<0) or np.any(uv[:,0]>=1280) or np.any(uv[:,1]>=720):
        raise ValueError('点击位置超出原始 720p 图像')
    if any(p.get('direction_quality',0)<.5 for p in points):raise ValueError('标定点需要有效板端方向')
    if len({(p.get('epoch'),p.get('doa_source_frame')) for p in points})!=10:
        raise ValueError('不能重复使用同一次定位结果')
    if min(np.ptp(xy[:6],axis=0))<.12 or np.ptp(uv[:6,0])<256 or np.ptp(uv[:6,1])<100:
        raise ValueError('位置分布太窄：需要左右和高低两个方向的变化')
    def normalize(v):
        mean=v.mean(axis=0);scale=math.sqrt(2)/np.linalg.norm(v-mean,axis=1).mean()
        t=np.array([[scale,0,-scale*mean[0]],[0,scale,-scale*mean[1]],[0,0,1]])
        return (v-mean)*scale,t
    a,ta=normalize(xy[:6]);b,tb=normalize(uv[:6]);rows=[]
    for (x,y),(u,v) in zip(a,b):
        rows.extend([[-x,-y,-1,0,0,0,u*x,u*y,u],[0,0,0,-x,-y,-1,v*x,v*y,v]])
    _,singular,vt=np.linalg.svd(rows)
    if singular[0]/max(singular[-2],1e-12)>1e4:raise ValueError('点位接近共线，无法建立稳定映射')
    h=np.linalg.inv(tb)@vt[-1].reshape(3,3)@ta
    if abs(h[2,2])<1e-9:raise ValueError('映射退化')
    h/=h[2,2]
    if abs(np.linalg.det(h))<1e-6:raise ValueError('映射退化')
    domain=hull(xy[:6].tolist())
    if not all(inside(p.tolist(),domain) for p in xy[6:]):
        raise ValueError('验证点应位于六个拟合点围出的范围内')
    denominators=xy@h[2,:2]+h[2,2]
    if np.any(denominators<=.1):raise ValueError('映射在测试区域内不连续')
    projected=np.array([_project_h(h.tolist(),p) for p in xy])
    errors=np.linalg.norm(projected-uv,axis=1)
    if np.sqrt(np.mean(errors[:6]**2))>30 or np.percentile(errors[6:],90)>35 or max(errors[6:])>70:
        raise ValueError(f'独立验证不通过：P90 {np.percentile(errors[6:],90):.1f}px，最大 {max(errors[6:]):.1f}px')
    return dict(model=MODEL,verified=False,size=[1280,720],homography=h.tolist(),domain=domain,
                points=points,train_rms_pixels=float(np.sqrt(np.mean(errors[:6]**2))),
                validation_p90_pixels=float(np.percentile(errors[6:],90)),validation_max_pixels=float(max(errors[6:])),
                scope='Measured angular overlay within training field only; finite-distance parallax and lens distortion are not independently calibrated.')


def validate_alignment(cal):
    if cal.get('model')!=MODEL or cal.get('verified') is not True or cal.get('size')!=[1280,720]:
        raise ValueError('需要经现场确认的 720p 声画对齐文件')
    if not cal.get('physical_confirmation'):raise ValueError('缺少现场方向确认')
    matrix=cal.get('homography',[])
    if len(matrix)!=3 or any(not isinstance(row,list) or len(row)!=3 for row in matrix):raise ValueError('无效对齐矩阵')
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for row in matrix for x in row):raise ValueError('无效对齐矩阵')
    # Recompute from independent training/validation observations, rather than
    # trust a JSON verified flag or an edited matrix alone.
    fitted=fit_alignment(cal.get('points',[]))
    if any(abs(x-y)>1e-6 for a,b in zip(cal.get('homography',[]),fitted['homography']) for x,y in zip(a,b)):
        raise ValueError('对齐矩阵与记录点不一致')
    if [list(p) for p in cal.get('domain',[])]!=[list(p) for p in fitted['domain']]:raise ValueError('对齐范围与记录点不一致')
    return cal


def project_quick(cal,azimuth,elevation,width,height):
    if not cal or cal.get('model')!=MODEL or not cal.get('verified') or cal.get('size')!=[width,height]:return None
    try:xy=ray_xy(azimuth,elevation)
    except ValueError:return None
    if not inside(xy,cal['domain']):return None
    uv=_project_h(cal['homography'],xy)
    return uv if uv and 0<=uv[0]<width and 0<=uv[1]<height else None


def select_markers(status,status_age):
    if not status or not math.isfinite(status_age) or not 0<=status_age<=1:return []
    result=[]
    age=status.get('direction_age_ms',10000)/1000+status_age
    quality=status.get('direction_quality',0)
    if math.isfinite(age) and 0<=age<=.4 and math.isfinite(quality) and 0<quality<=1:
        result.append(dict(kind='live' if quality>=.5 else 'weak',azimuth=status.get('azimuth'),
                           elevation=status.get('elevation'),quality=quality))
    if status.get('mode')=='LOCKED':
        result.append(dict(kind='locked',azimuth=status.get('locked_azimuth'),elevation=status.get('locked_elevation')))
    valid=[]
    for m in result:
        try:ray_xy(m['azimuth'],m['elevation']);valid.append(m)
        except (ValueError,TypeError):pass
    return valid


def pixel_from_widget(x,y,widget_size,image_size,raw_size=(1280,720)):
    """Label centres its thumbnail; clicks in letterbox margins are rejected."""
    ww,wh=widget_size;iw,ih=image_size
    left,top=(ww-iw)/2,(wh-ih)/2
    if iw<=0 or ih<=0 or not left<=x<left+iw or not top<=y<top+ih:return None
    return (x-left)*raw_size[0]/iw,(y-top)*raw_size[1]/ih


@lru_cache(maxsize=4)
def font(size):
    from PIL import ImageFont
    for p in ('C:/Windows/Fonts/msyh.ttc','C:/Windows/Fonts/simhei.ttf'):
        if Path(p).is_file():return ImageFont.truetype(p,size)
    return ImageFont.load_default()


def draw_overlay(image,markers,project,alignment_kind,notice=''):
    """Display image only; never mutate received RGB565 or recorded PNG/WAV."""
    from PIL import ImageDraw
    draw=ImageDraw.Draw(image);w,h=image.size;sx,sy=w/1280,h/720
    labels=[]
    for m in markers:
        color={'live':'#22d3ee','weak':'#fb923c','locked':'#fbbf24','reference':'#e9a099'}[m['kind']]
        name={'live':'声源','weak':'候选','locked':'锁定','reference':'参考 B（回执）'}[m['kind']]
        text=f"{name} {m['azimuth']:+.1f}° / {m['elevation']:+.1f}°"
        if 'quality' in m:text+=f"  {m['quality']:.0%}"
        uv=project(m['azimuth'],m['elevation']) if project else None
        if uv:
            x,y=uv[0]*sx,uv[1]*sy
            if m['kind']=='locked':
                draw.line((x-16,y,x+16,y),fill=color,width=3);draw.line((x,y-16,x,y+16),fill=color,width=3)
                draw.rectangle((x-12,y-12,x+12,y+12),outline=color,width=2)
            else:
                draw.ellipse((x-12,y-12,x+12,y+12),outline=color,width=3)
                if m['kind']=='live':draw.ellipse((x-2,y-2,x+2,y+2),fill=color)
            tx=min(max(4,x+17),max(4,w-100));ty=max(4,y-24)
            draw.text((tx,ty),name,fill=color,font=font(16),stroke_width=1,stroke_fill='#10110f')
        elif alignment_kind:text+=' · 画外或超出对齐范围'
        labels.append((text,color))
    heading=('声画快速对齐 · 仅在已验证范围内' if alignment_kind==MODEL else
             '声源位置 · 已标定' if alignment_kind else '方向示意 · 尚未声画对齐')
    lines=[(heading,'#e6e5dd')]+(labels or [('等待新鲜的板端方向','#abaea2')])
    if notice:lines.append((notice,'#fbbf24'))
    if w<80 or h<60:return
    box_width=min(w-16,max(260,max(draw.textlength(t,font=font(15)) for t,_ in lines)+20))
    draw.rounded_rectangle((8,8,8+box_width,18+len(lines)*24),radius=6,fill='#171816',outline='#3a3c34')
    for i,(text,color) in enumerate(lines):draw.text((18,14+i*24),text,fill=color,font=font(15))
