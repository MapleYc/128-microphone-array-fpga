from pathlib import Path
P=Path(__file__).resolve().parent
f=P/'monitor/meeting_av_monitor.py';s=f.read_text(encoding='utf-8')
needle='self.raw_preview=raw_preview if raw_preview is not None else RAW_PREVIEW_ADAPTER'
s=s.replace(needle,needle+"\n        self.audio_router=getattr(self.raw_preview,'playback_router',None)")
a=s.index('    def listen(self,mode):');b=s.index('    def set_listen_gain(self,value):',a)
s=s[:a]+'''    def listen(self,mode):
        if mode not in (0,2,3,4):raise ValueError('Invalid listen source')
        if self.audio_router is None:
            if mode==4:self.set_error('audio_source','PC enhancement is not enabled');return
            with self.play_lock:
                if self.player:self.player.close();self.player=None
                if mode:
                    from audio_playback import WaveOut
                    self.player=WaveOut()
                self.audio_mode=mode;self.listen_gain.reset();self.output_level=PcmLevel();self.listen_last={}
        else:
            with self.play_lock:self.audio_mode=0
            try:
                self.audio_router.select(mode)
                self.raw_preview.set_listen_mode(mode)
            except Exception as e:
                self.raw_preview.set_listen_mode(0);self.set_error('audio_source',e);return
            with self.play_lock:
                self.audio_mode=mode;self.listen_gain.reset();self.output_level=PcmLevel();self.listen_last={}
            with self.lock:
                self.errors.pop('host_preview',None);self.errors.pop('audio_source',None);self.errors.pop('playback',None)
        self._audio_event(type='listen_selection',mode=mode,
            source={0:'mute',2:'board_mono_16k',3:'board_beam_16k',4:'pc_enhancement_48k'}[mode])
    def audio_snapshot(self):
        route=self.audio_router.snapshot() if self.audio_router is not None else None
        gain=self.listen_gain.snapshot()
        if route and route['mode']==4:
            gain=dict(gain,gain_db=20*math.log10(self.audio_router.monitor_gain),limiter_reduction_db=0.,scope='PC48 monitor gain, separate from board16 gain')
        return dict(mode=self.audio_mode,raw=self.audio_levels[2].snapshot(),beam=self.audio_levels[3].snapshot(),
            player=route['player'] if route else self.player.snapshot() if self.player else None,
            output=route['output_level'] if route else self.output_level.snapshot(),gain=gain,
            board_processing=self.board_audio_processing.copy(),audio_route=route,
            pc_state=self.raw_preview.health() if self.raw_preview and hasattr(self.raw_preview,'health') else None)
''' +s[b:]
a=s.index('    def play_packet(self,msg):');b=s.index('    def record_audio_status(self):',a)
s=s[:a]+'''    def play_packet(self,msg):
        kind,_,frame,epoch,pcm=msg
        self.audio_levels[kind].feed(pcm)
        with self.play_lock:
            if self.audio_mode!=kind:return
            if self.audio_router is not None:
                ticket=self.audio_router.ticket()
                if self.listen_last.get(kind)!=(epoch,frame):self.listen_gain.reset()
                self.listen_last[kind]=(epoch,frame+3*(len(pcm)//2))
                audible=self.listen_gain.process(pcm);self.output_level.feed(audible)
                self.audio_router.feed_board(kind,audible,ticket)
                return
            if not self.player:return
            if self.listen_last.get(kind)!=(epoch,frame):self.listen_gain.reset()
            self.listen_last[kind]=(epoch,frame+3*(len(pcm)//2))
            audible=self.listen_gain.process(pcm);self.output_level.feed(audible);self.player.feed(audible)
''' +s[b:]
s=s.replace('        if self.raw_preview:self.raw_preview.close()','        if self.audio_router:self.audio_router.close()\n        if self.raw_preview:self.raw_preview.close()')
f.write_text(s,encoding='utf-8')
f=P/'meeting_dashboard.py';lines=f.read_text(encoding='utf-8').splitlines(keepends=True)
for i,l in enumerate(lines):
    if 'for mode,name in ((2,' in l:lines[i]="        for mode,name in ((2,'单麦参考16k'),(3,'板端定向16k'),(4,'PC128增强48k'),(0,'静音')):\n"
s=''.join(lines)
s=s.replace("if audio['mode']==0:play=", "if audio['mode']==4:\n            pc=audio.get('pc_state') or {};play='PC48: '+str(pc.get('state','waiting'))\n        elif audio['mode']==0:play=")
s=s.replace("self.play_status.configure(text=f'{play}","self.play_status.configure(text=f'{(audio.get(\"audio_route\") or {}).get(\"source\",\"legacy\")} | {play}")
f.write_text(s,encoding='utf-8')
f=P/'audio_status.py';s=f.read_text(encoding='utf-8')
s=s.replace("raw=status['raw'];beam=status['beam'];player=status.get('player')", "names[4]='PC128 enhancement48k (horizontal only)'\n    raw=status['raw'];beam=status['beam'];player=status.get('player')")
s=s.replace('selected=raw if mode==2 else beam',"selected=status.get('output',beam) if mode==4 else raw if mode==2 else beam")
f.write_text(s,encoding='utf-8')
print('monitor integration complete')
