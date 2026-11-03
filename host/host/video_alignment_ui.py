"""Interactive capture of real board-angle / camera-pixel correspondences."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
from datetime import datetime
import hashlib,json,time
from pathlib import Path
from video_direction_overlay import fit_alignment,validate_alignment,pixel_from_widget
from meeting_dashboard import BG,PANEL,TEXT,MUTED,VIDEO,FONT,button
class AlignmentDialog:
    def __init__(self,parent,capture,on_use,folder,*,save_images=False):
        import tkinter as tk
        from PIL import ImageTk
        self.tk=tk;self.ImageTk=ImageTk;self.capture=capture;self.on_use=on_use
        self.folder=Path(folder);self.points=[];self.frozen=None;self.candidate=None
        self.save_images=bool(save_images);self.image_digests=[]
        self.window=tk.Toplevel(parent);self.window.title('声画快速对齐 — 只映射板端角度')
        self.window.configure(bg=BG);self.window.transient(parent)
        self.message=tk.StringVar(value=self.instructions())
        tk.Label(self.window,textvariable=self.message,bg=BG,fg=TEXT,justify='left',wraplength=900,
                 font=(FONT,10)).pack(fill='x',padx=20,pady=16)
        self.picture=tk.Label(self.window,text='保持声源固定并持续讲话，点击“采集当前点”\n然后在冻结画面中点击嘴部或扬声器。',
                              bg=VIDEO,fg=MUTED,font=(FONT,10),width=100,height=24)
        self.picture.pack(padx=12,pady=4);self.picture.bind('<Button-1>',self.click)
        buttons=tk.Frame(self.window,bg=BG);buttons.pack(fill='x',padx=20,pady=16)
        self.take=button(buttons,'采集当前点',self.snapshot,accent=True);self.take.pack(side='left')
        button(buttons,'撤销最后一点',self.undo).pack(side='left',padx=8)
        self.enable=button(buttons,'确认左右/上下对应并启用',self.confirm)
        self.enable.configure(state='disabled')
        self.enable.pack(side='right')
        button(buttons,'关闭',self.window.destroy).pack(side='right',padx=8)
        self.window.protocol('WM_DELETE_WINDOW',self.window.destroy)
    def instructions(self):
        positions=['左下','中下','右下','左上','中上','右上','范围内左下（新位置）','范围内右下（新位置）',
                   '范围内左上（新位置）','范围内右上（新位置）']
        n=len(self.points)
        return ('SEARCH状态也可校准，无需先锁定。保持单声源持续说话；仅保存校准配置，不保存画面。\n'
                '六个拟合位置围住需要显示的区域；再用内部四个新位置独立验证。使用同一距离，声源保持静止；避免其他人说话。\n'
                '这只是视频坐标对齐，不改变板端定位、关键词识别或拾音方向。镜头/阵列相对位置变化后需重做。\n'+
                (f'已记录 {n}/10；下一点：{positions[n]}。质量需 ≥50%，且方向与图像均为新数据。' if n<10 else '已记录 10 点，正在检查独立验证误差。'))
    def snapshot(self):
        if len(self.points)>=10:return
        self.frozen=None
        try:
            image,metadata=self.capture()
            self.frozen=(image,metadata)
            thumb=image.copy();thumb.thumbnail((960,540));self.image_size=thumb.size
            self.picture.image=self.ImageTk.PhotoImage(thumb)
            self.picture.configure(image=self.picture.image,text='',width=0,height=0)
            self.message.set(self.instructions()+f"\n已冻结：{metadata['azimuth']:+.1f}° / {metadata['elevation']:+.1f}°；点击实际发声处。"+f"\n质量 {metadata['direction_quality']:.0%} · 方向年龄 {metadata.get('direction_age_ms',0):.0f}ms")
        except (ValueError,OSError) as exc:self.message.set(self.instructions()+'\n暂不能采集：'+str(exc))
    def click(self,event):
        if self.frozen is None:return
        image,metadata=self.frozen
        pixel=pixel_from_widget(event.x,event.y,(self.picture.winfo_width(),self.picture.winfo_height()),self.image_size,raw_size=image.size)
        if pixel is None:return
        key=(metadata['epoch'],metadata['doa_source_frame'])
        if key in [(p['epoch'],p['doa_source_frame']) for p in self.points]:
            self.message.set('这是重复的方向结果，请移动声源到新位置后重新采集。');return
        self.folder.mkdir(parents=True,exist_ok=True)
        path=self.folder/('point_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.png')
        digest=hashlib.sha256(image.tobytes()).hexdigest()
        self.image_digests.append(digest)
        if self.save_images:image.save(path)
        point=dict(metadata,pixel_x=pixel[0],pixel_y=pixel[1],split='train' if len(self.points)<6 else 'validation',
                   image_sha256=digest,evidence_storage='png' if self.save_images else 'volatile_rgb_not_saved',captured_at=time.time())
        if self.save_images:point['image']=str(path)
        self.points.append(point);self.frozen=None
        (self.folder/'points.yaml').write_text(store.dumps(self.points,indent=2,ensure_ascii=False),encoding='utf-8')
        self.message.set(self.instructions());self.enable.configure(state='disabled');self.candidate=None
        if len(self.points)==10:
            try:
                self.candidate=fit_alignment(self.points)
                (self.folder/'candidate.yaml').write_text(store.dumps(self.candidate,indent=2,ensure_ascii=False),encoding='utf-8')
                self.message.set(f"独立验证通过：P90 {self.candidate['validation_p90_pixels']:.1f}px，最大 {self.candidate['validation_max_pixels']:.1f}px。\n"
                                 '请确认十个点确实对应静止声源，左右/上下没有选反，再启用。仅在六个拟合点包围范围内显示标点；范围外不推算。')
                self.enable.configure(state='normal')
            except ValueError as exc:self.message.set('对齐未启用：'+str(exc)+'\n可撤销最后一点后重新采集，或关闭后重新开始。')
    def undo(self):
        if self.points:self.points.pop();self.image_digests.pop()
        self.candidate=None;self.frozen=None;self.enable.configure(state='disabled');self.message.set(self.instructions())
    def confirm(self):
        if not self.candidate:return
        try:
            for p,digest in zip(self.points,self.image_digests):
                if p['image_sha256']!=digest:
                    raise ValueError('原始画面证据发生变化，请重新采集')
            cal=dict(self.candidate,verified=True,physical_confirmation=dict(
                time=time.time(),method='User clicked distinct static sound-source positions and confirmed orientation in live UI',image_persistence=self.save_images))
            validate_alignment(cal);self.on_use(cal);self.window.destroy()
        except (ValueError,OSError) as exc:self.message.set('对齐未启用：'+str(exc))
