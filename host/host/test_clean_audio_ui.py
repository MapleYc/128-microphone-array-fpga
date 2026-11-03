import unittest
from types import SimpleNamespace
from ui_audio_controls import MAIN_CHOICES,DEBUG_CHOICES,build_audio_choices,build_debug_commands,playback_caption
from audio_source_router import AudioSourceRouter

class FakeButton:
    def __init__(self,parent,text,command):self.parent=parent;self.text=text;self.command=command
    def pack(self,**kw):self.packed=kw
    def invoke(self):return self.command()

class FakeSink:
    def __init__(self,mode):self.mode=mode;self.closed=False;self.fed=0
    def feed(self,data):self.fed+=len(data)//2 if self.mode!=4 else 512
    def close(self):self.closed=True
    def snapshot(self):return {'completed_samples':self.fed,'completion_age_seconds':0,'rate':48000 if self.mode==4 else 16000}

class CleanUI(unittest.TestCase):
    def test_main_buttons_dispatch_actual_routes(self):
        made=[]
        def sink(mode):p=FakeSink(mode);made.append(p);return p
        r=AudioSourceRouter(sink_factory=sink);seen=[]
        def listen(mode):seen.append(mode);r.select(mode)
        b=build_audio_choices('main',listen,FakeButton)
        self.assertEqual([(k,v.text) for k,v in b.items()],[(2,'单麦参考'),(4,'定向增强'),(0,'静音')])
        b[2].invoke();self.assertTrue(r.feed_board(2,b'\x01\x00'*128,r.ticket()))
        old=made[-1];b[4].invoke();self.assertTrue(old.closed)
        owner=r.register_pc_owner()
        self.assertTrue(r.prepare_pc(owner))
        import numpy as np
        self.assertTrue(r.feed_pc(np.zeros(512),r.ticket(),owner))
        self.assertEqual(r.snapshot()['source'],'pc_enhancement_48k')
        self.assertEqual(r.snapshot()['counts']['pc_enhancement_48k_fed_samples'],512)
        b[0].invoke();self.assertEqual(r.snapshot()['source'],'mute');self.assertTrue(made[-1].closed)
        self.assertEqual(seen,[2,4,0])
    def test_old_route_is_debug_only_and_real(self):
        seen=[];b=build_audio_choices('debug',seen.append,FakeButton,DEBUG_CHOICES)
        self.assertNotIn(3,dict(MAIN_CHOICES));self.assertEqual(list(b),[3]);b[3].invoke();self.assertEqual(seen,[3])
    def test_legacy_commands_remain_real_and_debug_scoped(self):
        commands=[];refs=[]
        controls=build_debug_commands('debug',commands.append,lambda:refs.append(True),FakeButton)
        for key in ('filter_on','filter_off','gain_on','gain_bypass','reference'):controls[key].invoke()
        self.assertEqual(commands,[b'n',b'o',b'g',b'b']);self.assertEqual(refs,[True])
        # Inspect the actual Dashboard method boundary without constructing Tk or opening devices.
        import ast,pathlib
        tree=ast.parse(pathlib.Path(__file__).with_name('meeting_dashboard.py').read_text(encoding='utf-8'))
        dashboard=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Dashboard')
        methods={n.name:n for n in dashboard.body if isinstance(n,ast.FunctionDef)}
        calls=lambda name:[n.func.id for n in ast.walk(methods[name]) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        self.assertNotIn('build_debug_commands',calls('__init__'))
        self.assertIn('build_debug_commands',calls('show_diagnostics'))
    def test_functional_reasons_survive_and_readiness_is_not_success(self):
        base={'mode':4,'player':None}
        def text(state):return playback_caption(dict(base,pc_state={'state':state}))
        self.assertIn('等待目标锁定',text('board_search_unlocked'))
        self.assertIn('俯仰角暂不支持',text('unsupported_nonzero_elevation'))
        self.assertIn('状态已过期',text('board_status_timeout'))
        self.assertIn('音频中断',text('raw_sequence_or_group_gap'))
        self.assertIn('增强准备中',text('dsp_warming'))
        self.assertIn('等待增强音频',text('horizontal_board_lock_active'))
        self.assertNotIn('生效',text('horizontal_board_lock_active'))
        self.assertEqual(playback_caption({'mode':0}),'已静音')
    def test_completed_playback_is_not_acoustic_acceptance(self):
        s={'mode':4,'player':{'completed_samples':512,'completion_age_seconds':0},'pc_state':{'state':'horizontal_board_lock_active'}}
        t=playback_caption(s);self.assertIn('正在播放',t);self.assertNotIn('抑制已生效',t)
    def test_recent_playback_cannot_hide_protection_or_fault(self):
        p={'completed_samples':512,'completion_age_seconds':0}
        cases=[({'state':'horizontal_board_lock_active','error':'bad'},'增强异常'),
               ({'state':'horizontal_board_lock_active','sticky_failure':True},'增强异常'),
               ({'state':'unsupported_nonzero_elevation'},'俯仰角暂不支持'),
               ({'state':'board_status_timeout'},'状态已过期'),
               ({'state':'board_search_unlocked'},'等待目标锁定'),
               ({'state':'raw_sequence_or_group_gap'},'音频中断')]
        for pc,expected in cases:
            with self.subTest(pc=pc):self.assertIn(expected,playback_caption({'mode':4,'player':p,'pc_state':pc}))

if __name__=='__main__':unittest.main()
