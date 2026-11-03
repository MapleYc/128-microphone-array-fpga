"""Run unchanged original8 assertions with legitimate atomic READY fixture.

The old fixture set only readyEvent. V5 state is authoritative, exactly as the
production worker publishes state+Event together under the shared lock.
"""
import os,sys,unittest
from pathlib import Path
P=Path(__file__).resolve().parent
os.environ['PROCESS_CANDIDATE_PATH']=str(P)
sys.path.insert(0,str(P.parent/'process_contract_tests_20261101_c58df0'))
import test_process_contract as tests
from process_adapter import READY
def atomic_ready_inbox(self):
    box=tests.SharedInbox(tests.mp.get_context('spawn'),1024)
    with box.lock:box.state.value=READY;box.ready.set()
    return box
tests.Contract.inbox=atomic_ready_inbox
if __name__=='__main__':unittest.main(module=tests)
