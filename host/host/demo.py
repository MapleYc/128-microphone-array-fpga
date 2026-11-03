"""Safe explicit entry. No args/preflight/help never opens a device."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
def main():
    args=sys.argv[1:]
    if not args:args=['preflight']
    if args[0] in ('--help','help','-h'):
        print('demo.py preflight [preview options]  # no NIC/UART/audio/GUI\n'
              'demo.py run --port COMx --seconds 60 --preview-inbox-packets 1024 --enable-playback [monitor options]\n'
              'Board mode follows actual azimuth and elevation; optional --dsp-process requires --preview-inbox-packets1024.\n'
              'Manual target: --preview-lock-source manual --preview-lock-deg 0 (does not follow board yes/no).')
        return
    if args[0] not in ('preflight','run'):raise SystemExit('Choose preflight or explicit run; no device opened')
    action=args.pop(0)
    import launcher
    sys.argv=[str(ROOT/'launcher.py'),'--host-preview','--preview-config',str(ROOT/'fixtures/P01.yaml'),*args]
    if action=='preflight':sys.argv.append('--preflight')
    launcher.main()
if __name__=='__main__':main()
