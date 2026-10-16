"""Unassigned Linux scan codes: separate presses, hold filtering, disabled actions."""
import _bootstrap
import tempfile
from pathlib import Path
from bose_ir import RemoteManager

with tempfile.TemporaryDirectory() as tmp:
    now = [0.0]
    m = RemoteManager(Path(tmp)/'state.json', lambda: now[0])
    sid = m.manage({'op':'begin','name':'Scan remote','model':'ir-remote'})['session_id']
    m.manage({'op':'capture','session_id':sid})
    for press in range(3):
        now[0] += 1
        assert m.scan_event(0x70707) is None
        for _ in range(10):
            now[0] += .117
            assert m.scan_event(0x70707) is None
        assert m.session['verified'] == press + 1
    assert m.session['phase'] == 'captured'
    m.manage({'op':'choose','session_id':sid,'choice':'keep'})
    for _ in range(2): m.manage({'op':'choose','session_id':sid,'choice':'skip'})
    rid = m.manage({'op':'commit','session_id':sid})['remote_id']
    now[0] += 1
    assert m.scan_event(0x70707) is None
    m.manage({'op':'update','remote_id':rid,'active':True,'key':'volume-up','enabled':True})
    now[0] += 1
    assert m.scan_event(0x70707) == 'volume-up'
    now[0] += .117
    assert m.scan_event(0x70707) == 'volume-up'
    now[0] += 1
    assert m.scan_event(0x12345) is None
    assert m.scan_event(True) is None
print('Unassigned scan codes, sustained repeats, three presses and disabled mappings passed')
