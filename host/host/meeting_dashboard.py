"""Tk presentation only: callbacks delegate to the existing Session unchanged."""
import math
import time
import tkinter as tk
from tkinter import scrolledtext
from direction_display import angle_point, direction_display_state
from audio_status import level_text
from keyword_display import keyword_presentation, threshold_percent
from interference_control import reference_command,reference_supported,filter_caption as wiener_caption
from reference_ui import ReferenceTransaction,angle_from_chart

BG = '#171816'
PANEL = '#22231f'
SURFACE = '#2c2d28'
HOVER = '#383a33'
VIDEO = '#10110f'
EDGE = '#3a3c34'
TEXT = '#f2f0e8'
MUTED = '#abaea2'
ACCENT = '#dfbb7b'
GOOD = '#afc8b2'
AMBER = '#efc276'
RED = '#e9a099'
FONT = 'Microsoft YaHei UI'


def label(parent, text='', size=10, color=TEXT, bold=False, **kw):
    return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=color,
                    font=(FONT, size, 'bold' if bold else 'normal'), **kw)


def button(parent, text, callback, accent=False):
    control = tk.Button(parent, text=text, command=callback,
                        bg=ACCENT if accent else SURFACE, fg=BG if accent else TEXT,
                        activebackground=HOVER, activeforeground=TEXT,
                        disabledforeground=MUTED, relief='flat', bd=0,
                        padx=10, pady=4, cursor='hand2', font=(FONT, 9),
                        highlightthickness=1, highlightbackground=EDGE, highlightcolor=ACCENT)
    control.selected = bool(accent)
    control.hovered = False
    def hover(inside):
        control.hovered = inside
        select_button(control, control.selected)
    control.bind('<Enter>', lambda _: hover(True))
    control.bind('<Leave>', lambda _: hover(False))
    return control


def select_button(control, selected):
    control.selected = bool(selected)
    control.configure(bg=ACCENT if selected else HOVER if control.hovered else SURFACE,
                      fg=BG if selected else TEXT,
                      activebackground=AMBER if selected else HOVER,
                      activeforeground=BG if selected else TEXT,
                      highlightbackground=ACCENT if selected else EDGE)


class Metric:
    def __init__(self, parent, title):
        self.frame = tk.Frame(parent, bg=PANEL, highlightthickness=0)
        self.frame.grid_columnconfigure(1, weight=1)
        self.title=label(self.frame, title, 9, MUTED)
        self.title.grid(row=0, column=0, sticky='w', padx=(12, 4), pady=(6, 0))
        self.value = label(self.frame, '—', 17, TEXT, True)
        self.value.grid(row=0, column=1, sticky='e', padx=(4, 12), pady=(4, 0))
        self.detail = label(self.frame, '等待数据', 8, MUTED, anchor='w')
        self.detail.grid(row=1, column=0, columnspan=2, sticky='ew', padx=12, pady=(0, 6))
        self.frame.bind('<Configure>', self._width)

    def _width(self, event):
        self.detail.configure(wraplength=max(110, event.width-24))

    def update(self, value, detail, color=TEXT):
        self.value.configure(text=value, fg=color)
        self.detail.configure(text=detail)

    def compact(self,enabled):
        self.value.configure(font=(FONT,15 if enabled else 17,'bold'))


class DirectionPanel:
    """Same tested angle/age rules, with a resizable plot and explicit legend."""
    def __init__(self, parent, reference_callback=None):
        self.frame = tk.Frame(parent, bg=PANEL, highlightthickness=0)
        self.frame.grid_rowconfigure(2, weight=1, minsize=150)
        self.frame.grid_columnconfigure(0, weight=1)
        self.heading=label(self.frame, '声源方向', 12, TEXT, True)
        self.heading.grid(row=0, sticky='w', padx=14, pady=(10, 0))
        self.subtitle=label(self.frame, '方位角 × 俯仰角', 9, MUTED)
        self.subtitle.grid(row=1, sticky='w', padx=14, pady=(2, 8))
        self.chart = tk.Canvas(self.frame, width=320, height=280, bg=VIDEO, bd=0, highlightthickness=0)
        self.chart.grid(row=2, sticky='nsew', padx=10)
        self.caption = label(self.frame, '等待方向数据', 9, TEXT, justify='left', anchor='w', height=3)
        self.caption.grid(row=3, sticky='ew', padx=14, pady=(6, 0))
        legend = self.legend = tk.Frame(self.frame, bg=PANEL)
        legend.grid(row=4, sticky='ew', padx=14, pady=(5, 4))
        for i, (text, color) in enumerate([('● 当前方向', '#22d3ee'), ('○ 低质量', '#fb923c'),
                                          ('○ 历史方向', '#94a3b8'), ('＋ 锁定方向', '#fbbf24')]):
            label(legend, text, 9, color).grid(row=i//2, column=i%2, sticky='w', padx=(0, 18))
        self.hint=label(self.frame, '正前方 0° / 0° · 视频位置需对齐', 8, MUTED)
        self.hint.grid(row=5, sticky='w', padx=14, pady=(0, 8))
        self.state = dict(current=None, locked=None, message='等待方向数据')
        self.bounds = (34, 16, 264, 134)
        self.chart.bind('<Configure>', self._resize)
        self.reference_callback=reference_callback
        self.reference_editable=False;self.reference_reason='等待参考控制能力回执'
        self.drag_reference=None
        self.chart.bind('<ButtonPress-3>',self._reference_begin)
        self.chart.bind('<B3-Motion>',self._reference_move)
        self.chart.bind('<ButtonRelease-3>',self._reference_release)
        self.chart.bind('<ButtonPress-1>',self._reference_handle_begin)
        self.chart.bind('<B1-Motion>',self._reference_move)
        self.chart.bind('<ButtonRelease-1>',self._reference_release)
        self.chart.bind('<Escape>',self._reference_cancel)

    def _reference_handle_begin(self,event):
        d=self.state.get('reference')
        if d:
            point=angle_point(d['azimuth'],d['elevation'],self.bounds)
            if point and math.hypot(event.x-point[0],event.y-point[1])<=18:self._reference_begin(event)

    def _reference_begin(self,event):
        if not self.reference_editable:
            self.caption.configure(text=self.reference_reason);return
        self.chart.focus_set()
        self.drag_reference=angle_from_chart(event.x,event.y,self.bounds)
        self._markers()

    def _reference_move(self,event):
        if self.drag_reference is None:return
        self.drag_reference=angle_from_chart(event.x,event.y,self.bounds)
        self._markers()

    def _reference_release(self,event):
        active=self.drag_reference is not None
        self.drag_reference=None;self._markers()
        point=angle_from_chart(event.x,event.y,self.bounds)
        if active and point and self.reference_callback:self.reference_callback(*point)

    def _reference_cancel(self,event=None):
        self.drag_reference=None;self._markers()

    def reference_capability(self,enabled,reason):
        self.reference_editable=bool(enabled);self.reference_reason=reason
        self.hint.configure(text='拖动红色 B；右键设置参考 B；松开提交' if enabled else reason)
        if not enabled:self._reference_cancel()

    def compact(self, enabled):
        self.heading.grid_configure(pady=(6 if enabled else 10,0))
        self.subtitle.grid_configure(pady=(1,4) if enabled else (2,8))
        self.caption.grid_configure(pady=(3 if enabled else 6,0))
        self.legend.grid_configure(pady=(2,3) if enabled else (5,4))
        self.hint.grid_configure(pady=(0,5 if enabled else 8))
        self.frame.grid_rowconfigure(2,minsize=110 if enabled else 150)

    def _resize(self, event):
        self.bounds = (34, 12, max(100, event.width-14), max(32, event.height-24))
        self.chart.delete('grid')
        left, top, right, bottom = self.bounds
        for az in (-80, -40, 0, 40, 80):
            x, _ = angle_point(az, 0, self.bounds)
            self.chart.create_line(x, top, x, bottom, fill='#5a5d51' if az == 0 else '#30322c', tags='grid')
            self.chart.create_text(x, bottom+14, text=str(az), fill=MUTED, font=(FONT, 8), tags='grid')
        for el in (-40, 0, 40):
            _, y = angle_point(0, el, self.bounds)
            self.chart.create_line(left, y, right, y, fill='#5a5d51' if el == 0 else '#30322c', tags='grid')
            self.chart.create_text(16, y, text=str(el), fill=MUTED, font=(FONT, 8), tags='grid')
        self._markers()

    def _markers(self):
        self.chart.delete('direction')
        d = self.state['current']
        if d:
            x, y = angle_point(d['azimuth'], d['elevation'], self.bounds)
            color = dict(reliable='#22d3ee', weak='#fb923c', history='#94a3b8')[d['kind']]
            self.chart.create_oval(x-6, y-6, x+6, y+6, outline=color, width=2,
                                   fill=color if d['kind'] == 'reliable' else '', tags='direction')
        d = self.state['locked']
        if d:
            x, y = angle_point(d['azimuth'], d['elevation'], self.bounds)
            self.chart.create_line(x-10, y, x+10, y, fill='#fbbf24', width=2, tags='direction')
            self.chart.create_line(x, y-10, x, y+10, fill='#fbbf24', width=2, tags='direction')
        d=self.state.get('reference')
        if d:
            x,y=angle_point(d['azimuth'],d['elevation'],self.bounds)
            self.chart.create_line(x-7,y-7,x+7,y+7,fill=RED,width=2,tags='direction')
            self.chart.create_line(x-7,y+7,x+7,y-7,fill=RED,width=2,tags='direction')
            self.chart.create_text(x,y-14,text='B 手动',fill=RED,font=(FONT,8),tags='direction')
        if self.drag_reference is not None:
            x,y=angle_point(*self.drag_reference,self.bounds)
            self.chart.create_oval(x-9,y-9,x+9,y+9,outline=AMBER,dash=(3,2),tags='direction')
            self.chart.create_text(x,y+16,text='B 松开提交',fill=AMBER,font=(FONT,8),tags='direction')

    def update(self, status, age):
        self.state = direction_display_state(status, age)
        df=status.get('directional_filter',{})
        if age is not None and age<1.5 and df.get('version')==3 and df.get('reference_valid'):
            self.state['reference']=dict(azimuth=df['reference_azimuth'],elevation=df['reference_elevation'])
        self.caption.configure(text=self.state['message'])
        self._markers()
        return self.state


class Meter:
    def __init__(self, parent, title):
        self.frame = tk.Frame(parent, bg=PANEL)
        self.title = title
        self.text = label(self.frame, title+'  —', 9, MUTED)
        self.text.pack(anchor='w')
        self.bar = tk.Canvas(self.frame, width=160, height=5, bg=EDGE, bd=0, highlightthickness=0)
        self.bar.pack(fill='x', pady=(4, 0))
        self.fill = self.bar.create_rectangle(0, 0, 0, 5, fill=ACCENT, width=0)

    def update(self, level, selected=False):
        self.text.configure(text=self.title+'  '+level_text(level), fg=TEXT if selected else MUTED)
        db = level.get('ac_dbfs')
        fresh = level.get('age_seconds') is not None and level['age_seconds'] <= 1
        fraction = max(0, min(1, (db+90)/90)) if fresh and isinstance(db, (float,int)) and math.isfinite(db) else 0
        width = max(160, self.bar.winfo_width())
        self.bar.coords(self.fill, 0, 0, width*fraction, 5)
        self.bar.itemconfigure(self.fill, fill=AMBER if fraction>.95 else GOOD if selected else '#707b68')


from ui_audio_controls import MAIN_CHOICES, DEBUG_CHOICES, build_audio_choices, build_debug_commands, playback_caption, gain_control_state

class Dashboard:
    def __init__(self, root, args, callbacks):
        self.root, self.args, self.callbacks = root, args, callbacks
        self.diagnostics = None
        self.diagnostic_text = ''
        self.toast = ''
        self.toast_until = 0
        self.closed = False
        self.controls = {}
        self.reference_transaction=ReferenceTransaction(callbacks['command'])
        self.reference_report={};self.reference_report_age=None;self.reference_epoch=None
        self.reference_feedback={'state':'unsupported','message':'等待参考控制能力回执'}
        self._last_gain = float(args.listen_gain_db)
        self._syncing_gain=False
        self._gain_control=gain_control_state(dict(mode=0,gain=dict(gain_db=args.listen_gain_db)))
        root.title('Mini200 · 声场工作台')
        root.configure(bg=BG)
        self.window_icon=tk.PhotoImage(master=root,width=32,height=32)
        self.window_icon.put(BG,to=(0,0,32,32))
        for x,extent in ((5,10),(12,18),(19,24),(26,14)):
            self.window_icon.put(ACCENT,to=(x,16-extent//2,x+3,16+extent//2))
        root.iconphoto(True,self.window_icon)
        # Leave room for the title bar/taskbar under Windows display scaling.
        width, height = min(1600, root.winfo_screenwidth()-70), min(980, root.winfo_screenheight()-100)
        width, height = max(960,width), max(660,height)
        root.geometry(f'{width}x{height}+20+20')
        root.minsize(960, 660)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)
        root.protocol('WM_DELETE_WINDOW', callbacks['close'])
        header = self.header = tk.Frame(root, bg=BG)
        header.grid(row=0, sticky='ew', padx=18, pady=(8, 8))
        wordmark = tk.Frame(header,bg=BG)
        wordmark.pack(side='left',padx=(0,10))
        mark = tk.Canvas(wordmark,width=38,height=32,bg=BG,highlightthickness=0)
        mark.pack()
        for x, extent in ((6,8),(14,14),(22,18),(30,10)):
            mark.create_line(x,19-extent/2,x,19+extent/2,fill=ACCENT,width=3,capstyle='round')
        title = tk.Frame(header, bg=BG); title.pack(side='left')
        self.heading=label(title, '声场工作台', 16, TEXT, True)
        self.heading.pack(anchor='w')
        self.subtitle=label(title, 'ARRAY 128   /   定向拾音与声画监看', 9, MUTED)
        self.badge = label(header, '● 等待连接', 10, MUTED, padx=16, pady=8)
        self.badge.configure(bg=PANEL)
        self.badge.pack(side='right')
        self.device_label=label(header,'MINI200  /  RISC-V',9,MUTED)
        self.device_label.pack(side='right',padx=18)
        metrics = self.metrics_frame = tk.Frame(root, bg=EDGE); metrics.grid(row=1, sticky='ew', padx=18, pady=(0, 8))
        self.metrics = []
        for i, name in enumerate(('拾音状态', '关键词识别', '方向质量', '视频显示')):
            metrics.grid_columnconfigure(i, weight=1, uniform='metrics')
            m = Metric(metrics, name); m.frame.grid(row=0,column=i,sticky='nsew',padx=(0,1 if i<3 else 0))
            self.metrics.append(m)
        self.keyword_minimum = threshold_percent(getattr(args, 'keyword_display_min', 85))
        keyword_settings = tk.Frame(header, bg=BG)
        keyword_settings.pack(side='right', padx=12)
        label(keyword_settings, '关键词显示 ≥', 9, MUTED).pack(side='left')
        self.keyword_value = tk.StringVar(value=f'{self.keyword_minimum:g}')
        self.keyword_spin = tk.Spinbox(keyword_settings, from_=0, to=100, increment=1,
            width=4, textvariable=self.keyword_value, command=self._keyword_changed,
            bg=SURFACE, fg=TEXT, buttonbackground=SURFACE, insertbackground=TEXT,
            relief='flat', highlightthickness=1, highlightbackground=EDGE, font=(FONT, 9))
        self.keyword_spin.pack(side='left', padx=6)
        label(keyword_settings, '%', 9, MUTED).pack(side='left')
        self.keyword_spin.bind('<Return>', lambda _: self._keyword_changed())
        self.keyword_spin.bind('<FocusOut>', lambda _: self._keyword_changed())
        self.controls['keyword_threshold'] = self.keyword_spin
        body = self.body = tk.Frame(root, bg=BG); body.grid(row=2, sticky='nsew', padx=18)
        body.grid_columnconfigure(0,weight=1);body.grid_columnconfigure(1,minsize=340);body.grid_rowconfigure(0,weight=1)
        video = self.video_panel = tk.Frame(body,bg=PANEL,highlightthickness=0)
        video.grid(row=0,column=0,sticky='nsew',padx=(0,10))
        video.grid_rowconfigure(1,weight=1);video.grid_columnconfigure(0,weight=1)
        top = tk.Frame(video,bg=PANEL);top.grid(row=0,sticky='ew',padx=12,pady=5)
        label(top,'现场视频',11,TEXT,True).pack(side='left')
        self.video_badge=label(top,'等待摄像头',9,MUTED);self.video_badge.pack(side='right')
        if 'alignment' in callbacks:
            self.controls['alignment']=button(top,'声画对齐',callbacks['alignment'])
            self.controls['alignment'].pack(side='right',padx=12)
        self.viewport = tk.Frame(video,bg=VIDEO,width=max(240,width-410),height=max(150,height-310))
        self.viewport.grid(row=1,sticky='nsew',padx=1);self.viewport.grid_propagate(False)
        self.viewport.grid_columnconfigure(0,weight=1);self.viewport.grid_rowconfigure(0,weight=1)
        self.canvas = tk.Label(self.viewport,text='等待摄像头画面\n\n1280 × 720  /  连接板卡后开始监看',bg=VIDEO,fg=MUTED,
                               font=(FONT,13),bd=0,width=1,height=1)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        self.note=label(video,'实时画面 · 声源定位 · 定向增强',9,MUTED,anchor='w')
        self.note.grid(row=2,sticky='ew',padx=12,pady=5)
        self.direction=DirectionPanel(body,self.request_reference);self.direction.frame.grid(row=0,column=1,sticky='nsew')

        audio=self.audio_panel=tk.Frame(root,bg=PANEL,highlightthickness=0)
        audio.grid(row=3,sticky='ew',padx=18,pady=(8,6))
        line=tk.Frame(audio,bg=PANEL);line.pack(fill='x',padx=12,pady=(6,3))
        label(line,'音频交互',11,TEXT,True).pack(side='left',padx=(0,20))
        selector=tk.Frame(line,bg=VIDEO,padx=3,pady=3);selector.pack(side='left')
        self.listen_buttons=build_audio_choices(selector,callbacks['listen'],button)
        for mode,b in self.listen_buttons.items():self.controls['listen_'+str(mode)]=b
        self.play_status=label(line,'点击选择监听 · 无需先锁定',9,MUTED);self.play_status.pack(side='right')
        self.reference_values=(-45,0,0)
        self.reference_window=None
        self.reference_ready=False
        self.filter_caption=None
        gainline=self.gainline=tk.Frame(audio,bg=PANEL);gainline.pack(fill='x',padx=12,pady=(0,3))
        self.gain_caption=label(gainline,f'监听增益 +{args.listen_gain_db:.0f} dB',10,ACCENT,True,width=22,anchor='w')
        self.gain_caption.pack(side='left')
        self.gain_value=tk.DoubleVar(value=args.listen_gain_db)
        self.gain_value.trace_add('write',lambda *_:self._gain_changed(self.gain_value.get()))
        self.scale=tk.Scale(gainline,from_=0,to=48,orient='horizontal',resolution=1,variable=self.gain_value,
                            showvalue=False,length=200,sliderlength=18,width=6,
                            bd=0,relief='flat',highlightthickness=0,bg=PANEL,fg=TEXT,troughcolor=EDGE,
                            activebackground=ACCENT)
        self.scale.pack(side='left',fill='x',expand=True,padx=(0,10))
        for db in (0,12,24,36,48):
            b=button(gainline,str(db),lambda v=db:self.set_gain(v));b.pack(side='left',padx=2)
            self.controls['gain_'+str(db)]=b
        meters=tk.Frame(audio,bg=PANEL);meters.pack(fill='x',padx=12,pady=(1,7))
        self.meters=[]
        self.meter_keys=('raw','output')
        for i,name in enumerate(('参考输入','当前输出')):
            meters.grid_columnconfigure(i,weight=1,uniform='meter')
            m=Meter(meters,name);m.frame.grid(row=0,column=i,sticky='ew',padx=(0,16 if i<1 else 0));self.meters.append(m)

        footer=tk.Frame(root,bg=BG);footer.grid(row=4,sticky='ew',padx=18,pady=(0,8))
        self.connection=label(footer,f'{args.port}  ·  {args.bind}',9,MUTED);self.connection.pack(side='left')
        self.remaining=label(footer,'',9,MUTED);self.remaining.pack(side='left',padx=12)
        for key,name,callback in [('unlock','解锁目标',lambda:callbacks['command'](b'u')),
                                  ('recognition','识别开关对照',lambda:callbacks['command'](b'i')),
                                  ('save','保存标定图',callbacks['save']),
                                  ('diagnostics','诊断详情',self.show_diagnostics)]:
            b=button(footer,name,callback);b.pack(side='right',padx=(6,0));self.controls[key]=b
        self.fallback_size=(max(240,width-410),max(150,height-310))
        self.compact=None
        root.bind('<Configure>',self._layout,add='+')
        root.update_idletasks()

    def _layout(self,event):
        if event.widget is not self.root:return
        compact=event.height<760 or event.width<1200
        if compact==self.compact:return
        self.compact=compact
        self.header.grid_configure(pady=(5,5) if compact else (8,8))
        self.heading.configure(font=(FONT,14 if compact else 16,'bold'))
        self.body.grid_columnconfigure(1,minsize=280 if compact else 340)
        self.direction.chart.configure(width=260 if compact else 320)
        if compact:self.device_label.pack_forget()
        else:self.device_label.pack(side='right',padx=18)
        for metric in self.metrics:metric.compact(compact)
        self.direction.compact(compact)

    def _gain_changed(self,value):
        if getattr(self,'_syncing_gain',False):return False
        spec=getattr(self,'_gain_control',dict(enabled=True,min_db=0,max_db=48))
        try:value=float(value)
        except (TypeError,ValueError):return False
        if not spec['enabled'] or not math.isfinite(value) or not spec['min_db']<=value<=spec['max_db']:return False
        self.gain_caption.configure(text=f'监听增益 +{value:.0f} dB')
        if value!=self._last_gain:
            self._last_gain=value
            self.callbacks['gain'](value)
            return True
        return False

    def _update_gain_controls(self,audio):
        spec=gain_control_state(audio);self._gain_control=spec
        self.scale.configure(from_=spec['min_db'],to=spec['max_db'],state='normal' if spec['enabled'] else 'disabled')
        for db in (0,12,24,36,48):
            allowed=spec['enabled'] and spec['min_db']<=db<=spec['max_db']
            self.controls['gain_'+str(db)].configure(state='normal' if allowed else 'disabled')
        # Programmatic reflection of another source's remembered value must not
        # invoke the setter and accidentally overwrite either source's gain.
        self._syncing_gain=True
        try:
            self._last_gain=spec['value']
            if self.gain_value.get()!=spec['value']:self.gain_value.set(spec['value'])
        finally:self._syncing_gain=False
        self.gain_caption.configure(text=spec['caption'])

    def reference_dialog(self):
        if self.reference_window is not None and self.reference_window.winfo_exists():
            self.reference_window.lift();return
        win=tk.Toplevel(self.root);self.reference_window=win
        win.title('干扰方向 B');win.configure(bg=PANEL);win.resizable(False,False);win.transient(self.root)
        label(win,'目标 A 由 YES 锁定，干扰 B 由你指定。',11,TEXT,True).grid(row=0,columnspan=2,padx=20,pady=(18,5))
        label(win,'按右侧方向图的角度填写；与目标至少相隔约 15°。\n设置会发给 FPGA，不在电脑端处理声音。',9,MUTED,justify='left').grid(row=1,columnspan=2,padx=20,pady=(0,12))
        variables=[tk.StringVar(value=str(v)) for v in self.reference_values]
        for row,(name,low,high,var) in enumerate(zip(('方位角（度）','俯仰角（度）','强度档位（0～4）'),(-80,-40,0),(80,40,4),variables),2):
            label(win,name,10).grid(row=row,column=0,sticky='w',padx=20,pady=4)
            tk.Spinbox(win,from_=low,to=high,textvariable=var,width=8,bg=SURFACE,fg=TEXT,buttonbackground=SURFACE,
                       insertbackground=TEXT,relief='flat',font=(FONT,11)).grid(row=row,column=1,padx=20,pady=4)
        feedback=label(win,'先从低档 0 开始，对比目标清晰度。提高档位可能同时压低目标；完整旁路请选“抑制关”。',9,MUTED,wraplength=380,justify='left')
        feedback.grid(row=5,columnspan=2,padx=20,pady=10)
        def apply():
            if not self.reference_ready:
                feedback.configure(text='尚未收到新版双波束位流的有效回执，请先下载本版位流并恢复连接。',fg=RED);return
            try:
                values=tuple(int(v.get()) for v in variables);cmd=reference_command(*values)
            except (ValueError,TypeError) as exc:feedback.configure(text=str(exc),fg=RED);return
            sent=self.request_reference(*values)
            feedback.configure(text=self.reference_transaction.message,fg=AMBER if sent else RED)
        button(win,'发送到板端',apply,True).grid(row=6,columnspan=2,pady=(0,18))

    def request_reference(self,azimuth,elevation,strength=None):
        if strength is None:strength=self.reference_values[2]
        sent=self.reference_transaction.submit(round(azimuth),round(elevation),int(strength),
            self.reference_report,self.reference_report_age,self.reference_epoch,time.monotonic())
        if sent:self.reference_values=(round(azimuth),round(elevation),int(strength))
        self.show_message(self.reference_transaction.message,time.monotonic())
        return sent

    def set_gain(self,value):
        spec=getattr(self,'_gain_control',dict(enabled=True,min_db=0,max_db=48))
        try:value=float(value)
        except (TypeError,ValueError):return False
        if not spec['enabled'] or not math.isfinite(value) or not spec['min_db']<=value<=spec['max_db']:return False
        # Variable trace and preset dispatch share the existing one-dispatch guard.
        previous=self._last_gain
        self.gain_value.set(value)
        changed=self._gain_changed(value)
        return changed or value!=previous

    def video_size(self):
        w,h=self.viewport.winfo_width(),self.viewport.winfo_height()
        if w<=2 or h<=2:w,h=self.fallback_size
        return max(1,min(1280,w-2)),max(1,min(720,h-2))

    def _keyword_changed(self):
        try:
            value = threshold_percent(self.keyword_value.get())
        except (ValueError, TypeError):
            self.keyword_value.set(f'{self.keyword_minimum:g}')
            return
        if value != self.keyword_minimum:
            self.keyword_minimum = value
            callback = self.callbacks.get('keyword_threshold')
            if callback: callback(value)

    def video_interrupted(self):
        self.canvas.configure(image='',text='视频信号已中断\n\n等待新的完整画面')
        self.canvas.image=None

    def show_message(self,text,now):
        self.toast=text;self.toast_until=now+4

    def show_diagnostics(self):
        if self.diagnostics is not None and self.diagnostics.winfo_exists():
            self.diagnostics.lift();return
        self.diagnostics=tk.Toplevel(self.root);self.diagnostics.title('Mini200 · 诊断详情')
        self.diagnostics.configure(bg=BG);self.diagnostics.geometry('860x520')
        debug_audio=tk.Frame(self.diagnostics,bg=BG);debug_audio.pack(fill='x',padx=12,pady=(8,0))
        label(debug_audio,'调试音源（独立于主界面增强）',9,MUTED).pack(side='left',padx=(0,8))
        self.debug_listen_buttons=build_audio_choices(debug_audio,self.callbacks['listen'],button,DEBUG_CHOICES)
        debug_processing=tk.Frame(self.diagnostics,bg=BG);debug_processing.pack(fill='x',padx=12,pady=(6,0))
        self.controls.update(build_debug_commands(debug_processing,self.callbacks['command'],self.reference_dialog,button))
        self.filter_caption=label(self.diagnostics,'',9,MUTED,anchor='w')
        self.filter_caption.pack(fill='x',padx=12,pady=(5,0))
        self.diagnostic_view=scrolledtext.ScrolledText(self.diagnostics,bg=PANEL,fg=TEXT,insertbackground=TEXT,
            font=(FONT,10),wrap='word',relief='flat',padx=16,pady=16)
        self.diagnostic_view.pack(fill='both',expand=True,padx=12,pady=12)
        self._diagnostic_update()

    def _diagnostic_update(self):
        if self.diagnostics is None or not self.diagnostics.winfo_exists():return
        view=self.diagnostic_view;position=view.yview();view.configure(state='normal')
        view.delete('1.0','end');view.insert('1.0',self.diagnostic_text);view.configure(state='disabled')
        view.yview_moveto(position[0])

    def update(self,*,status,video,status_age,frame_age,fps,display_fps,audio,errors,diagnostic_text,elapsed,now,calibrated):
        fresh=bool(status) and status_age<=1
        mode=status.get('mode') if fresh else None
        self.badge.configure(text='● 系统在线' if fresh else '● 等待连接',fg=GOOD if fresh else MUTED)
        policy=audio.get('board_processing',{})
        rule=(f"{policy['yes_votes_required']}次 yes + {policy['directions_required']}次有效方向" if policy else '等待系统规则回执')
        from command_gate import guard_caption,release_caption
        guard=status.get('command_guard',{}) if fresh else {}
        release=release_caption(guard) if mode=='SEARCH' else ''
        self.metrics[0].update('已锁定' if mode=='LOCKED' else '搜索中' if mode else '等待连接',release or rule,AMBER if mode=='LOCKED' else GOOD if fresh else MUTED)
        keyword_text, keyword_detail = keyword_presentation(status, self.keyword_minimum, fresh)
        self.metrics[1].update(keyword_text, '命令动作以系统回执为准' if fresh else keyword_detail,
                               GOOD if keyword_text.startswith('YES') else TEXT)
        state=self.direction.update(status,status_age)
        current=state['current']
        self.metrics[2].update(f"{current['quality']:.0%}" if current else '—',
            f"方位 {current['azimuth']:+.1f}° / 俯仰 {current['elevation']:+.1f}°" if current else '等待有效方向',
            dict(reliable=TEXT,weak=AMBER,history=MUTED)[current['kind']] if current else MUTED)
        valid_video=frame_age is not None and frame_age<=1
        self.metrics[3].update(f'{display_fps:.1f} fps' if valid_video else '—',f'1280 × 720 · 接收 {fps:.1f} fps',TEXT if valid_video else MUTED)
        self.video_badge.configure(text='● 实时' if valid_video else '等待摄像头' if frame_age is None else '信号中断',fg=GOOD if valid_video else MUTED)
        self.note.configure(text=self.toast if now<self.toast_until else ('连接异常，请查看诊断详情' if errors else
            '声画已快速对齐 · 仅在验证范围内显示落点' if calibrated=='measured_angular_homography_v1' else
            '阵列方向与视频已标定' if calibrated else '视频内显示角度 · 点击“声画对齐”启用实际落点'),fg=RED if errors else MUTED)
        for selected,b in self.listen_buttons.items():
            select_button(b,selected==audio['mode'])
        if hasattr(self,'debug_listen_buttons') and self.diagnostics is not None and self.diagnostics.winfo_exists():
            for selected,b in self.debug_listen_buttons.items():select_button(b,selected==audio['mode'])
        self._update_gain_controls(audio)
        gain=audio.get('gain',{})
        limiting=gain.get('limiter_reduction_db',0)
        self.play_status.configure(text=playback_caption(audio)+(f' · 限幅 {limiting:.1f} dB' if limiting>1 else ''),fg=AMBER if limiting>1 else MUTED)
        df=status.get('directional_filter',{}) if fresh else {}
        filter_age=status.get('directional_filter_age') if fresh else None
        self.reference_ready=fresh and reference_supported(df,filter_age,status.get('epoch'))
        self.reference_report=df;self.reference_report_age=filter_age;self.reference_epoch=status.get('epoch')
        previous_reference_state=self.reference_feedback.get('state')
        self.reference_feedback=self.reference_transaction.observe(df,filter_age,self.reference_epoch,now)
        if self.reference_feedback['state']!=previous_reference_state and self.reference_feedback['state'] not in ('idle','unsupported'):
            self.show_message(self.reference_feedback['message'],now)
        self.direction.reference_capability(self.reference_ready,self.reference_feedback['message'])
        timed_out=not df and isinstance(filter_age,(int,float)) and filter_age>1.5
        caption=(f'抑制回执超时 {filter_age:.0f}秒 · 运行状态待确认' if timed_out else
                 '自适应抑制 · 等待系统回执' if not df else
                 '板端抑制已关闭 · 延时求和对照' if not df['enabled'] else
                 '板端抑制已开启 · 锁定后生效' if not df['locked'] else
                 '板端抑制已开启 · 等待同步参考' if not df['primed'] else
                 '板端目标保护 · 当前保留定向原声' if df['target_protected'] else
                 '板端自适应抵消运行中 · 与“抑制关”同增益对照')
        if df.get('version')==3:caption=wiener_caption(df)
        if self.diagnostics is not None and self.diagnostics.winfo_exists():
            self.filter_caption.configure(text=caption,fg=RED if df.get('faults') else AMBER if timed_out or df.get('clipped') else GOOD if df.get('primed') and df.get('enabled') else MUTED)
            for key,on in [('filter_on',1),('filter_off',0)]:select_button(self.controls[key],bool(df) and df['enabled']==on)
        for m,key in zip(self.meters,self.meter_keys):
            m.update(audio[key],selected=(key=='output' and audio['mode']!=0))
        remaining=max(0,int(self.args.seconds-elapsed))
        self.remaining.configure(text=f'记录剩余 {remaining//60:02d}:{remaining%60:02d}')
        self.connection.configure(text='连接异常 · 查看诊断' if errors else f'{self.args.port}  ·  {self.args.bind}',fg=RED if errors else MUTED)
        self.diagnostic_text=diagnostic_text+'\n\n日志目录：'+str(self.args.out)+'\n\n当前演示：采集、视频、KWS与定位来自FPGA；主界面定向增强使用已验收PC动态链（48k）；调试入口保留原板端定向16k。完整FPGA增强仍为后续版本。真实后端状态和采样率保留在日志。'
        self.diagnostic_text+='\n\n参考B控制：'+str(self.reference_feedback)
        self._diagnostic_update()
