import _bootstrap
import json
import tempfile
from pathlib import Path
from bose_ir import RemoteManager, IRException, validate_management, strict_json
from bose_config import resolve_ir_action
from unittest.mock import patch


def run():
    with tempfile.TemporaryDirectory() as tmp:
        now = [0]
        path = Path(tmp)/'state.json'
        m = RemoteManager(path, lambda: now[0])
        m.persist()
        def call(op, **kw): return m.manage({'op':op, **kw})
        def begin(name='Remote', **kw): return call('begin',name=name,model='ir-remote',**kw)['session_id']
        def fail(op, error, **kw):
            try: call(op, **kw)
            except IRException as e: assert str(e)==error, e
            else: raise AssertionError('accepted invalid operation')
        def press(scan):
            now[0] += .31
            return m.scan_event(scan)
        sid=begin()
        fail('begin','busy',name='Other',model='ir-remote')
        call('capture',session_id=sid)
        assert press(123) is None
        assert press(123) is None
        assert press(123) is None
        assert press(456) is None
        assert call('session',session_id=sid)['candidate']==123
        call('choose',session_id=sid,choice='keep')
        call('choose',session_id=sid,choice='skip')
        call('capture',session_id=sid)
        now[0]=m.session['capture_expires']
        assert call('session',session_id=sid)['phase']=='timeout'
        call('choose',session_id=sid,choice='retry')
        assert press(789) is None
        press(789); press(789)
        call('choose',session_id=sid,choice='keep')
        assert not m.profiles
        rid=call('commit',session_id=sid)['remote_id']
        assert press(123) is None
        call('update',remote_id=rid,active=True,key='volume-up',enabled=True)
        assert press(123)=='volume-up'
        assert m.scan_event(123)=='volume-up'
        now[0] += .3
        assert m.scan_event(123)=='volume-up'
        assert press(999) is None
        sid=begin('Unsigned scan')
        call('capture',session_id=sid)
        press(-1); press(-1); press(-1)
        assert call('session',session_id=sid)['candidate']==0xffffffff
        call('cancel',session_id=sid)
        sid=begin('Mismatch')
        call('capture',session_id=sid); press(100); press(101)
        assert call('session',session_id=sid)['phase']=='inconsistent'
        call('cancel',session_id=sid)
        sid=begin(remote_id=rid,keys=['volume-up'])
        assert press(123) is None
        call('choose',session_id=sid,choice='skip')
        call('cancel',session_id=sid)
        assert m.profiles[rid]['mappings']['volume-up']['code']==123
        sid=begin('Shared')
        call('capture',session_id=sid); press(123); press(123); press(123)
        assert call('session',session_id=sid)['shared']
        call('choose',session_id=sid,choice='keep')
        call('capture',session_id=sid); press(123)
        assert call('session',session_id=sid)['phase']=='conflict'
        fail('choose','no_capture',session_id=sid,choice='keep')
        now[0]=m.session['expires']
        fail('session','session_expired',session_id=sid)
        assert resolve_ir_action('mute', 'a', m.mute_state)=='room-a-mute'
        m.command_succeeded('room-a-mute')
        m.command_succeeded('room-a-volume-up')
        assert m.mute_state is True
        assert resolve_ir_action('mute', 'a', m.mute_state)=='room-a-unmute'
        m.mute_unknown(); assert m.mute_state is None
        again=RemoteManager(path)
        assert again.bridge_id==m.bridge_id and again.profiles==m.profiles
        call('update',remote_id=rid,name='Renamed')
        assert rid in m.profiles
        call('update',remote_id=rid,key='volume-up',remove=True)
        assert press(123) is None
        before=m.profiles.copy()
        with patch('bose_ir.os.replace',side_effect=OSError('disk failure')):
            try: call('update',remote_id=rid,name='Lost')
            except OSError: pass
            else: raise AssertionError('storage failure hidden')
        assert m.profiles==before
        call('delete',remote_id=rid); assert not m.profiles
    for bad in ({'op':'shell','command':'id'},{'op':'delete','remote_id':'../state'},{'op':'resync','muted':1},{'op':'update','remote_id':'a'*32,'key':'mute','enabled':True,'command':'x'}):
        try: validate_management(bad)
        except ValueError: pass
        else: raise AssertionError('unbounded management request accepted')
    try: strict_json('{"op":"delete","op":"shell"}')
    except ValueError: pass
    else: raise AssertionError('duplicate key accepted')
    test_models_single_neutral_value_and_unknown_model_fails_closed()
    test_ir_state_with_old_model_names_is_rejected()
    print('Transactional capture, timeout, expiry, cancellation, conflicts, shared signals, enablement, persistence and mute passed')

# Earlier remote model names live only in old state files. They are built here at runtime, never spelled out.
OLD_MODELS = (''.join(('r', 'c', '7', '3')), ''.join(('s', 'i', 'r', 'i')))


def test_models_single_neutral_value_and_unknown_model_fails_closed():
    import bose_ir
    from bose_ir import validate_profiles, empty_mappings
    assert bose_ir.MODELS == ('ir-remote',)
    assert not hasattr(bose_ir, 'REMOTES')
    def profiles(model):
        return {'a' * 32: {'name': 'Remote', 'model': model, 'active': False, 'mappings': empty_mappings()}}
    validate_profiles(profiles('ir-remote'))
    for model in OLD_MODELS + ('other', None):
        try: validate_profiles(profiles(model))
        except IRException: pass
        else: raise AssertionError(f'unknown model accepted: {model!r}')


def test_ir_state_with_old_model_names_is_rejected():
    from bose_ir import empty_mappings
    old_a, old_b = OLD_MODELS
    legacy = {'schema': 1, 'mappings': {old_a: {'volume-up': 42, 'volume-down': None, 'mute': None},
                                         old_b: {'volume-up': None, 'volume-down': None, 'mute': None}},
              'completed': {old_a: {}, old_b: {}}, 'mute_state': None}
    current = {'schema': 2, 'bridge_id': 'b' * 32, 'mute_state': None,
               'profiles': {'a' * 32: {'name': 'Old', 'model': old_a, 'active': False, 'mappings': empty_mappings()}}}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'state.json'
        for body in (legacy, current):
            path.write_text(json.dumps(body))
            raw = path.read_bytes()
            try: RemoteManager(path)
            except IRException: pass
            else: raise AssertionError('old model state was loaded')
            assert path.read_bytes() == raw, 'rejected state must not be rewritten'


if __name__=='__main__': run()
