"""Run with HA 2026.9.4's Python: python test_bose_integration.py."""
import asyncio
import base64
import copy
import json
import struct
import logging
import re
from pathlib import Path
import shutil
import tempfile
import time
from unittest.mock import patch
from homeassistant.core import HomeAssistant, Context
from homeassistant import auth
from homeassistant.auth.const import GROUP_ID_USER, GROUP_ID_ADMIN
from homeassistant.exceptions import Unauthorized
from homeassistant.components.config.config_entries import SubentryManagerFlowIndexView, SubentryManagerFlowResourceView
from homeassistant import loader, bootstrap
from homeassistant.config_entries import ConfigEntries
from homeassistant.helpers import entity_registry as er, device_registry as dr

def registered_buttons(registry, entry, bridge_id):
    """unique key (without the bridge prefix) -> registry entry, for this config entry's buttons."""
    prefix=f'{bridge_id}_'
    return {item.unique_id[len(prefix):]:item for item in er.async_entries_for_config_entry(registry,entry.entry_id)
            if item.domain=='button' and item.unique_id.startswith(prefix) and item.unique_id!=f'{bridge_id}_fault-clear'}

from custom_components.bose_lifestyle import pairing
from custom_components.bose_lifestyle.client import Client, BridgeError, validate_status

FIXTURE_V4 = json.loads((Path(__file__).parent/'tests/fixtures/status_v4.json').read_text())
EXCLUDED = {'identify', 'system-power-on', 'system-power-off'}   # SCHEMA4_EXCLUDED: never entities

from bose_ir import RemoteManager

async def run():
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(Path(__file__).parent/'custom_components',Path(tmp)/'custom_components',ignore=shutil.ignore_patterns('__pycache__'))
        hass=HomeAssistant(tmp)
        loader.async_setup(hass)
        hass.config_entries=ConfigEntries(hass,{})
        await hass.config_entries.async_initialize()
        manager=RemoteManager(Path(tmp)/'ir.json'); manager.persist()
        serial=[]
        fault=[None]
        fault=[None]; fault_clears=[]; d_refuse=[False]; outcome_data=[None]; events_data=[{'t':1.5,'kind':'tx','action':'volume-down','detail':'4d 4d'}]; bridge_calls=[]
        cfg=copy.deepcopy(FIXTURE_V4['config'])   # the bridge host's config.json, as its status reports it
        status_override=[None]   # when set, returns a status built by this function
        bridge_fail=[None]           # (op, bridge host error code) to make one config op fail
        def apply_config(payload):
            if payload['op']=='set_zone': cfg['zones'][payload['zone']]['enabled']=payload['enabled']
            elif payload['op']=='set_unverified': cfg['unverified'][payload['zone']]=payload['allow']
            elif payload['op']=='set_source': cfg['sources'][payload['source']]=payload['connected']
            elif payload['op']=='set_volume_steps':
                if payload['steps']==1: cfg.pop('volume_steps',None)
                else: cfg['volume_steps']=payload['steps']
            else: cfg['ir']['zone']=payload['zone']
        def live_status():
            data=copy.deepcopy(FIXTURE_V4)
            data.update(bridge_id=manager.bridge_id,generated_at=time.time(),config=copy.deepcopy(cfg),ir=manager.status())
            data['bridge'].update(mode='live',serial_open=True,fault=fault[0],last_outcome=outcome_data[0])
            for name,info in data['actions'].items():   # the allowed rule the bridge host applies (checked against the fixture)
                zone=info['zone']
                info['allowed']=bool(cfg['zones'][zone]['enabled'] is True and (info['verified'] or cfg['unverified'][zone] is True)
                                     and (info['function']!='source' or cfg['sources'].get(info['source']) is True)
                                     and fault[0] is None)   # the bridge publishes allowed false while faulted
            return data
        async def fake_call(self,action,payload=None):
            if action=='fault-clear':
                fault_clears.append(action)
                fault[0]=None
                return 'fault cleared'
            if action=='events': return list(events_data)
            if action=='manage' and payload['op'] in ('set_zone','set_unverified','set_ir_zone','set_source','set_volume_steps'):
                bridge_calls.append(payload)
                if bridge_fail[0] and bridge_fail[0][0]==payload['op']: raise BridgeError(bridge_fail[0][1])
                apply_config(payload)
                return {}
            if action=='manage':
                try: return manager.manage(payload)
                except ValueError as error: raise BridgeError(str(error)) from error
            if action=='status' and status_override[0] is not None:
                return validate_status(json.dumps(status_override[0]()))
            if action=='status':
                return validate_status(json.dumps(live_status()))
            if d_refuse[0] and action.startswith('room-d-'): raise BridgeError('refused_by_unit')
            serial.append(action)
            from bose_commands import LIVE_MESSAGES
            return LIVE_MESSAGES[action]
        await bootstrap.async_load_base_functionality(hass)
        hass.auth=await auth.auth_manager_from_config(hass,[],[])
        await hass.auth.async_create_user('Admin',group_ids=[GROUP_ID_ADMIN])
        operator=await hass.auth.async_create_user('Operator',group_ids=[GROUP_ID_USER])
        assert not operator.is_admin
        for view in (SubentryManagerFlowIndexView,SubentryManagerFlowResourceView):
            try: await view.post(None,{'hass_user':operator})
            except Unauthorized: pass
            else: raise AssertionError('normal user could manage profiles')
        records=[]
        class Capture(logging.Handler):
            def emit(self,record): records.append((record.levelno,record.getMessage()))
        bose_logger=logging.getLogger('custom_components.bose_lifestyle'); bose_logger.addHandler(Capture()); bose_logger.setLevel(logging.DEBUG)
        def logged(level,text): return [m for l,m in records if l==level and text in m]
        registry=er.async_get(hass)
        # Pre-existing registry entries (an earlier install): the same unique IDs, neutral object IDs. Setup must keep them.
        SEEDED={'room-a-mute':'bose_controller_example_mute','room-a-power-on':'bose_controller_example_power_on'}
        for key,object_id in SEEDED.items():
            registry.async_get_or_create('button','bose_lifestyle',f'{manager.bridge_id}_{key}',suggested_object_id=object_id)
        with patch.object(Client,'call',fake_call):
            result=await hass.config_entries.flow.async_init('bose_lifestyle',context={'source':'user'})
            assert result['step_id']=='key',result
            result=await hass.config_entries.flow.async_configure(result['flow_id'],{})
            assert result['step_id']=='host',result
            scanned='bridge.example.invalid ssh-ed25519 '+base64.b64encode(struct.pack('>I',11)+b'ssh-ed25519'+struct.pack('>I',32)+bytes(range(32))).decode()
            with patch.object(pairing,'scan_host_key',lambda host,port,runner=None: scanned):
                result=await hass.config_entries.flow.async_configure(result['flow_id'],{'host':'bridge.example.invalid','user':'bose','port':22})
            assert result['step_id']=='fingerprint',result
            result=await hass.config_entries.flow.async_configure(result['flow_id'],{'fingerprint':pairing.fingerprint_of(scanned)})
            assert result['type']=='create_entry',result
            await hass.async_block_till_done()
            entry=result['result']
            assert [s.title for s in entry.subentries.values()]==['Controller','Zones','Remotes']
            groups={s.subentry_type:s.subentry_id for s in entry.subentries.values()}
            options=await hass.config_entries.options.async_init(entry.entry_id)
            assert options['step_id']=='init',options
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'fault_message':'line one\nline two'})
            assert options['type']=='form' and options['errors']['base']=='invalid_fault_message',options
            options=await hass.config_entries.options.async_configure(options['flow_id'],{'fault_message':'Use P1 <carefully>'})
            assert options['type']=='create_entry',options
            await hass.async_block_till_done()
            assert entry.options['fault_message']=='Use P1 <carefully>'
            assert entry.runtime_data.fresh
            vol_up=registry.async_get_entity_id('button','bose_lifestyle',f'{manager.bridge_id}_room-a-volume-up')
            bid=dr.async_get(hass).async_get_device_by_identifier(('bose_lifestyle',manager.bridge_id),entry.entry_id).id
            fault_clear_id=registry.async_get_entity_id('button','bose_lifestyle',f'{manager.bridge_id}_fault-clear')
            assert fault_clear_id and registry.async_get(fault_clear_id).device_id==bid
            assert registry.async_get(fault_clear_id).config_subentry_id==groups['controller']
            wanted=set(FIXTURE_V4['actions'])-EXCLUDED
            for action in FIXTURE_V4['actions']:   # every status action passes the client gate once status names it
                gate=Client({'host':'bridge.example.invalid','user':'bose','port':22}); gate.dynamic=frozenset(FIXTURE_V4['actions'])
                gate._gate(action)
            # test_unique_ids_unchanged_for_actions: one button per status action, unique ID {bridge_id}_{key}, no legacy IDs
            pinned_now={k:(i.unique_id,i.entity_id) for k,i in registered_buttons(registry,entry,manager.bridge_id).items()}
            assert set(pinned_now)==wanted and len(wanted)==52,(sorted(set(pinned_now)^wanted))
            assert all(uid==f'{manager.bridge_id}_{k}' for k,(uid,_) in pinned_now.items())
            # test_legacy_entity_ids_kept_when_device_renamed: the device display name changed, the legacy entity ids and unique IDs did not,
            # no entity was removed and no _2 duplicate appeared.
            legacy_entries=registered_buttons(registry,entry,manager.bridge_id)
            assert len(legacy_entries)==52
            ids={item.entity_id for item in registry.entities.values() if item.platform=='bose_lifestyle'}
            dupes=[e for e in ids if e.endswith('_2') and e[:-2] in ids]   # a real '_2' (Video 2) is fine; a copy of a registered id is not
            assert dupes==[],dupes
            for key,object_id in SEEDED.items():   # existing entries are adopted under the same entity_id: no rename, no _2
                assert registry.async_get_entity_id('button','bose_lifestyle',f'{manager.bridge_id}_{key}')==f'button.{object_id}',key
            assert dr.async_get(hass).async_get(bid).name=='Bose Controller'
            room_d=dr.async_get(hass).async_get_device_by_identifier(('bose_lifestyle',f'{manager.bridge_id}_room_d'),entry.entry_id)
            assert room_d.name=='Zone D' and room_d.model=='Zone' and room_d.via_device_id==bid
            assert room_d.config_subentry_id==groups['zones']
            assert dr.async_get(hass).async_get(bid).config_subentry_id==groups['controller']
            d_power=registry.async_get_entity_id('button','bose_lifestyle',f'{manager.bridge_id}_room-d-power-off')
            assert d_power and hass.states.get(d_power).state=='unavailable'
            assert registry.async_get(d_power).device_id==room_d.id
            for room,name in (('b','Zone B'),('c','Zone C')):
                device=dr.async_get(hass).async_get_device_by_identifier(('bose_lifestyle',f'{manager.bridge_id}_room_{room}'),entry.entry_id)
                assert device.name==name and device.via_device_id==bid
                assert device.config_subentry_id==groups['zones']
                volume=registry.async_get_entity_id('button','bose_lifestyle',f'{manager.bridge_id}_room-{room}-volume-up')
                assert registry.async_get(volume).config_subentry_id==groups['zones']
                assert volume and hass.states.get(volume).state!='unavailable'
            assert hass.states.get(vol_up).state!='unavailable'
            assert entry.title=='Bose Controller'
            assert dr.async_get(hass).async_get(bid).name=='Bose Controller'
            fault_id=registry.async_get_entity_id('binary_sensor','bose_lifestyle',f'{manager.bridge_id}_fault')
            assert hass.states.get(fault_id).state=='off'
            records.clear()
            fault[0]='partial-write'; await entry.runtime_data.async_refresh(); await hass.async_block_till_done()
            await entry.runtime_data.async_refresh()   # still faulted: no repeat log
            assert len(logged(logging.ERROR,'partial-write'))==1 and len(logged(logging.ERROR,'Recent Bose events'))==1,records
            assert hass.states.get(fault_id).state=='on' and hass.states.get(fault_id).attributes['fault']=='partial-write'
            assert hass.states.get(fault_clear_id).state!='unavailable'
            sent_before_clear=len(serial)
            await hass.services.async_call('button','press',{'entity_id':fault_clear_id},blocking=True)
            await hass.async_block_till_done()
            assert fault_clears==['fault-clear'] and len(serial)==sent_before_clear
            assert len(logged(logging.INFO,'fault cleared'))==1 and len(logged(logging.ERROR,'partial-write'))==1,records
            assert hass.states.get(fault_id).state=='off' and hass.states.get(fault_clear_id).state=='unavailable'
            assert hass.states.get(registry.async_get_entity_id('sensor','bose_lifestyle',f'{manager.bridge_id}_health')).attributes['fault_message']=='Use P1 <carefully>'
            # Transition-only logging: a refusal logs once, an outage logs once however many polls fail.
            records.clear()
            outcome_data[0]={'action':'room-d-mute','outcome':'refused-by-m1','observed_at':time.time()}
            for _ in range(3): await entry.runtime_data.async_refresh()
            assert len(logged(logging.WARNING,'refused'))==1 and 'room-d-mute' in logged(logging.WARNING,'refused')[0],records
            outcome_data[0]=None
            records.clear()
            failing=[True]
            polls=[0]
            async def flaky(self,action,payload=None):
                if failing[0]:
                    polls[0]+=1
                    raise BridgeError('stale_status' if polls[0]%2==0 else 'cannot_connect')   # two different errors alternating
                return await fake_call(self,action,payload)
            with patch.object(Client,'call',flaky):
                for _ in range(30): await entry.runtime_data.async_refresh()
                failing[0]=False
                await entry.runtime_data.async_refresh()
            assert len(logged(logging.ERROR,'Error fetching'))==1 and len(logged(logging.INFO,'recovered'))==1,records
            assert len(logged(logging.WARNING,'error changed'))==1,records   # each distinct error is reported once per 10 minutes
            await hass.async_block_till_done()
            # Card backend: layout for everyone, settings/events/set_room for administrators only.
            from custom_components.bose_lifestyle import websocket as bws
            from custom_components.bose_lifestyle.card import async_register_card
            import types
            class Conn:
                def __init__(self,admin): self.user=types.SimpleNamespace(is_admin=admin,name='tester'); self.results=[]; self.errors=[]
                def send_result(self,id,result=None): self.results.append(result)
                def send_error(self,id,code,message): self.errors.append(code)
                def async_handle_exception(self,msg,err): self.errors.append(type(err).__name__)
            async def ws(handler,admin,**msg):
                conn=Conn(admin)
                try: handler(hass,conn,{'id':1,**msg})
                except Unauthorized: conn.errors.append('unauthorized')   # HA's dispatcher turns this into an unauthorized error
                await hass.async_block_till_done(wait_background_tasks=True); return conn
            registered=hass.data['websocket_api']
            assert {'bose_lifestyle/layout','bose_lifestyle/settings','bose_lifestyle/set_room','bose_lifestyle/events','bose_lifestyle/set_source','bose_lifestyle/set_room_name','bose_lifestyle/set_bridge_name','bose_lifestyle/set_experimental','bose_lifestyle/set_input','bose_lifestyle/set_ir_receiver_label'} <= set(registered)
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            layout=conn.results[0]
            assert [r['id'] for r in layout['rooms']]==['a','b','c'] and not conn.errors
            assert layout['rooms'][0]['buttons']['volume-up']==vol_up
            assert layout['health']==registry.async_get_entity_id('sensor','bose_lifestyle',f'{manager.bridge_id}_health')
            assert layout['outcome']==registry.async_get_entity_id('sensor','bose_lifestyle',f'{manager.bridge_id}_outcome')
            assert all(registry.async_get(e) for r in layout['rooms'] for e in r['buttons'].values())
            for handler,msg in ((bws.ws_settings,{}),(bws.ws_set_room,{'room':'a','name':'Lounge'}),(bws.ws_events,{})):
                conn=await ws(handler,False,**msg)
                assert conn.errors==['unauthorized'] and not conn.results,(handler,conn.errors)
            conn=await ws(bws.ws_settings,True,type='bose_lifestyle/settings')
            assert [(r['id'],r['enabled']) for r in conn.results[0]['rooms']]==[('a',True),('b',True),('c',True),('d',False)]
            bridge_calls.clear()
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='a',name=' Lounge ')
            assert [r['name'] for r in conn.results[0]['rooms']][0]=='Lounge' and not bridge_calls
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='b',enabled=False)
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert [(r['id'],r['name'],r['enabled']) for r in conn.results[0]['rooms']]==[('a','Lounge',True),('c','Room C',True)] and bridge_calls==[{'op':'set_zone','zone':'b','enabled':False}]   # only the card hides disabled zones
            await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='b',enabled=True)
            bridge_calls.clear()
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='e',enabled=True)
            assert conn.errors==['invalid_request'] and not bridge_calls
            records.clear()
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='d',enabled=True)    # Room D follows the bridge host
            assert len(logged(logging.INFO,'Room D live permission enabled by tester'))==1,records
            assert bridge_calls==[{'op':'set_zone','zone':'d','enabled':True},{'op':'set_unverified','zone':'d','allow':True}] and cfg['zones']['d']['enabled'] is True
            assert [r['enabled'] for r in conn.results[0]['rooms']][3] is True
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            d_room=conn.results[0]['rooms'][3]
            assert d_room['id']=='d' and set(d_room['buttons'])=={'power-on','power-off','volume-up','volume-down','mute','unmute','tv','music','source-am','source-video-2'}
            assert hass.states.get(d_room['buttons']['mute']).state!='unavailable'
            assert hass.states.get(registry.async_get_entity_id('button','bose_lifestyle',f'{manager.bridge_id}_room-d-source-fm')).state=='unavailable'   # not connected
            sent=len(serial)
            await hass.services.async_call('button','press',{'entity_id':d_room['buttons']['mute']},blocking=True,context=Context(user_id=operator.id))
            assert serial[sent:]==['room-d-mute']; del serial[sent:]
            d_refuse[0]=True
            try: await hass.services.async_call('button','press',{'entity_id':d_room['buttons']['mute']},blocking=True,context=Context(user_id=operator.id))
            except Exception as error: assert 'refused_by_unit' in str(error),error
            else: raise AssertionError('M1 refusal reported as success')
            d_refuse[0]=False
            bridge_fail[0]=('set_zone','cannot_connect')
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='d',enabled=False)
            assert conn.errors==['bridge_error'] and cfg['zones']['d']['enabled'] is True
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert [r['id'] for r in conn.results[0]['rooms']]==['a','b','c','d']               # unchanged after the failed toggle
            bridge_fail[0]=None
            await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='d',enabled=False)
            assert cfg['zones']['d']['enabled'] is False
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert [r['id'] for r in conn.results[0]['rooms']]==['a','b','c']
            assert dr.async_get(hass).async_get(room_d.id) is not None
            assert registry.async_get(d_room['buttons']['mute']).device_id==room_d.id
            assert hass.states.get(d_room['buttons']['mute']).state=='unavailable'
            conn=await ws(bws.ws_events,True,type='bose_lifestyle/events',limit=10)
            assert conn.results[0]==events_data
            with patch.object(Client,'call',lambda self,action,payload=None: (_ for _ in ()).throw(BridgeError('cannot_connect'))):
                conn=await ws(bws.ws_events,True,type='bose_lifestyle/events',limit=10)
                assert conn.errors==['bridge_error']
            with patch.object(bws,'loaded_entry',lambda hass: None):
                conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
                assert conn.errors==['not_ready']
            # A card or websocket registration failure must never stop Bose control itself from loading.
            import custom_components.bose_lifestyle as bose_init
            records.clear()
            with patch('custom_components.bose_lifestyle.card.async_register_card',lambda h: (_ for _ in ()).throw(RuntimeError('frontend missing'))):
                assert await bose_init.async_setup(hass,{}) is True
            assert len(logged(logging.WARNING,'Bose card'))==1,records
            # Card file is served once with a cache-busting version; no HTTP component means a quiet skip.
            served=[]
            class Http:
                async def async_register_static_paths(self,paths): served.extend(paths)
            fake_hass=types.SimpleNamespace(http=Http(),async_add_executor_job=hass.async_add_executor_job)
            urls=[]
            with patch('homeassistant.components.frontend.add_extra_js_url',lambda h,url: urls.append(url)):
                await async_register_card(fake_hass)
                await async_register_card(types.SimpleNamespace(async_add_executor_job=hass.async_add_executor_job))
            version=json.loads((Path(__file__).parent/'custom_components/bose_lifestyle/manifest.json').read_text())['version']
            assert [(c.url_path,Path(c.path).name,c.cache_headers) for c in served]==[('/bose_lifestyle_static','frontend',False)]
            assert urls==[f'/bose_lifestyle_static/bose-control-card.js?v={version}'] and (Path(served[0].path)/'bose-control-card.js').exists()
            def press(code):
                manager.event(1,115,0); manager.event(4,4,code); assert manager.event(1,115,1) is None
            assert registry.async_get_entity_id('binary_sensor','bose_lifestyle',f'{manager.bridge_id}_receiver'),'receiver sensor unique id changed'
            flows=hass.config_entries.subentries
            flow=await flows.async_init((entry.entry_id,'remotes'),context={'source':'user'})
            assert flow['step_id']=='name' and manager.session is None,flow
            assert [str(k) for k in flow['data_schema'].schema]==['name'],'remote flow has a model selector'
            assert (await flows.async_configure(flow['flow_id'],{'name':'x'*41}))['errors']=={'base':'name_length'}
            flow=await flows.async_configure(flow['flow_id'],{'name':'Test Remote'})
            fid=flow['flow_id']
            assert flow['step_id']=='prepare'
            flow=await flows.async_configure(fid,{'choice':'listen'})
            assert flow['type']=='progress',flow
            # Concurrent receiver lease is rejected.
            other=await flows.async_init((entry.entry_id,'remotes'),context={'source':'user'})
            other=await flows.async_configure(other['flow_id'],{'name':'Concurrent'})
            assert other['errors']['base']=='busy'
            flows.async_abort(other['flow_id'])
            press(123); assert manager.session['verified']==1
            manager.event(4,4,123); manager.event(1,115,2)
            assert manager.session['verified']==1
            press(123); press(123)
            await asyncio.sleep(.5)
            flow=await flows.async_configure(fid)
            if flow['type']=='progress_done': flow=await flows.async_configure(fid)
            assert flow['step_id']=='detected',flow
            flow=await flows.async_configure(fid,{'choice':'keep'})
            flow=await flows.async_configure(fid,{'choice':'skip'})
            flow=await flows.async_configure(fid,{'choice':'skip'})
            assert flow['step_id']=='review'
            assert not manager.profiles and not serial
            flow=await flows.async_configure(fid,{})      # Save remote commits the profile; the ready check follows
            assert flow['step_id']=='check',flow
            saved=copy.deepcopy(manager.profiles)
            assert len(saved)==1
            flow=await flows.async_configure(fid,{})      # Start the ready check
            assert flow['type']=='progress',flow
            press(999); await asyncio.sleep(.5)           # a different button never ticks the check
            flow=await flows.async_configure(fid)
            if flow['type']=='progress_done': flow=await flows.async_configure(fid)
            assert flow['step_id']=='check_detected',flow
            flow=await flows.async_configure(fid,{})      # Try again
            assert flow['type']=='progress',flow
            press(123); await asyncio.sleep(.5)
            flow=await flows.async_configure(fid)
            if flow['type']=='progress_done': flow=await flows.async_configure(fid)
            assert flow['step_id']=='check_detected',flow
            flow=await flows.async_configure(fid,{})      # the saved button was pressed: the check finishes
            assert flow['type']=='abort' and flow['reason']=='reconfigure_successful',flow
            assert manager.session is None and manager.profiles==saved,'the ready check changed the saved profile'
            await hass.async_block_till_done()
            assert len(entry.subentries)==3
            sub=next(s for s in entry.subentries.values() if s.subentry_type=='remotes'); rid=next(iter(sub.data['profiles']))
            assert not manager.profiles[rid]['active'] and not manager.profiles[rid]['mappings']['volume-up']['enabled']
            device=dr.async_get(hass).async_get_device_by_identifier(('bose_lifestyle',rid),entry.entry_id)
            assert device.via_device_id==bid and device.model=='Remote'
            assert not serial
            # Native reconfigure enables one mapping; profile identity survives rename.
            flow=await flows.async_init((entry.entry_id,'remotes'),context={'source':'reconfigure','subentry_id':sub.subentry_id})
            flow=await flows.async_configure(flow['flow_id'],{'remote_id':rid})
            flow=await flows.async_configure(flow['flow_id'],{'operation':'mapping'})
            flow=await flows.async_configure(flow['flow_id'],{'key':'volume-up','operation':'enable'})
            assert manager.profiles[rid]['mappings']['volume-up']['enabled']
            flow=await flows.async_init((entry.entry_id,'remotes'),context={'source':'reconfigure','subentry_id':sub.subentry_id})
            flow=await flows.async_configure(flow['flow_id'],{'remote_id':rid})
            flow=await flows.async_configure(flow['flow_id'],{'operation':'rename'})
            flow=await flows.async_configure(flow['flow_id'],{'name':'Renamed'})
            await hass.async_block_till_done()
            assert sub.title=='Remotes' and rid in sub.data['profiles'] and manager.profiles[rid]['name']=='Renamed'
            assert dr.async_get(hass).async_get(device.id).name=='Renamed'
            assert device.config_subentry_id==sub.subentry_id
            # Abort discards staging and releases lease.
            flow=await flows.async_init((entry.entry_id,'remotes'),context={'source':'user'})
            flow=await flows.async_configure(flow['flow_id'],{'name':'Cancel'})
            flows.async_abort(flow['flow_id'])
            await hass.async_block_till_done()
            assert manager.session is None
            # Removing HA registration must leave bridge host profile available for import.
            hass.config_entries.async_update_subentry(entry,sub,data={'profiles':{}})
            await hass.async_block_till_done()
            assert rid in manager.profiles
            flow=await flows.async_init((entry.entry_id,'remotes'),context={'source':'user'})
            assert flow['step_id']=='user'
            flow=await flows.async_configure(flow['flow_id'],{'profile':rid})
            await hass.async_block_till_done()
            sub=next(s for s in entry.subentries.values() if s.subentry_type=='remotes')
            flow=await flows.async_init((entry.entry_id,'remotes'),context={'source':'reconfigure','subentry_id':sub.subentry_id})
            flow=await flows.async_configure(flow['flow_id'],{'remote_id':rid})
            flow=await flows.async_configure(flow['flow_id'],{'operation':'delete'})
            flow=await flows.async_configure(flow['flow_id'],{})
            await hass.async_block_till_done()
            assert rid not in manager.profiles and not sub.data['profiles'] and len(entry.subentries)==3
            assert not serial
            # Normal users can operate the ordinary entity service.
            await hass.services.async_call('button','press',{'entity_id':vol_up},blocking=True,context=Context(user_id=operator.id))
            assert serial==['room-a-volume-up']
            room_b_off=registry.async_get_entity_id('button','bose_lifestyle',f'{manager.bridge_id}_room-b-power-off')
            await hass.services.async_call('button','press',{'entity_id':room_b_off},blocking=True,context=Context(user_id=operator.id))
            assert serial==['room-a-volume-up','room-b-power-off']
            # Stale health makes controls unavailable.
            entry.runtime_data.read_at=time.monotonic()-16
            entry.runtime_data.async_update_listeners()
            await hass.async_block_till_done()
            assert hass.states.get(vol_up).state=='unavailable'
            # Status-driven buttons: schema 4 from the live coordinator, config changes and faults. Entities are never removed.
            # test_new_entities_added_on_schema_4_without_restart (also: old IDs and names unchanged)
            def v4(mutate=None):
                data=live_status()
                if mutate: mutate(data)
                return data
            async def show(mutate=None):
                status_override[0]=lambda: v4(mutate)
                await entry.runtime_data.async_refresh(); await hass.async_block_till_done()
            def buttons(): return registered_buttons(registry,entry,manager.bridge_id)
            def ids(): return {k:item.entity_id for k,item in buttons().items()}
            def states(): return {k:hass.states.get(item.entity_id).state for k,item in buttons().items()}
            devices=dr.async_get(hass)
            bridge_device=devices.async_get_device_by_identifier(('bose_lifestyle',entry.unique_id),entry.entry_id)
            room_a_device=devices.async_get_device_by_identifier(('bose_lifestyle',f'{entry.unique_id}_room_a'),entry.entry_id)
            assert bridge_device and room_a_device and bridge_device.id!=room_a_device.id
            room_d_device=devices.async_get_device_by_identifier(('bose_lifestyle',f'{entry.unique_id}_room_d'),entry.entry_id)
            assert bridge_device.model=='Controller'
            assert room_a_device.via_device_id==bridge_device.id and room_a_device.name=='Zone A' and room_a_device.model=='Zone'
            assert room_d_device.name=='Zone D' and room_d_device.model=='Zone'
            before=ids()
            assert len(before)==52
            await show()
            after=ids()
            assert after==before
            assert before['room-a-mute']=='button.bose_controller_example_mute'
            assert registry.async_get(after['room-a-source-am']).original_name=='AM'
            assert registry.async_get(after['room-a-source-video-2']).original_name=='Video 2'
            assert dr.async_get(hass).async_get(registry.async_get(after['room-b-source-am']).device_id).name=='Zone B'
            # test_disallowed_buttons_unavailable_keep_registry_entries
            assert states()['room-a-source-cd']=='unavailable' and states()['room-a-source-fm']=='unavailable'
            assert states()['room-a-source-am']!='unavailable' and states()['room-a-mute']!='unavailable'
            # test_no_entity_deleted_on_config_change: a zone disabled on the bridge host goes unavailable and keeps its registry entries
            def zone_b_off(data):
                data['config']['zones']['b']['enabled']=False
                for action in data['actions'].values():
                    if action['zone']=='b': action['allowed']=False
            await show(zone_b_off)
            assert ids()==after
            assert all(v=='unavailable' for k,v in states().items() if k.startswith('room-b-')) and states()['room-c-mute']!='unavailable' and states()['room-a-mute']!='unavailable'
            # test_no_entity_deleted_on_config_change: config null or actions {} never deletes an entity
            def config_null(data):
                data.update(config=None,actions={})
                data['bridge']['config']='config-invalid'
            await show(config_null)
            assert ids()==after and set(states().values())=={'unavailable'}
            # test_fault_makes_all_buttons_unavailable
            def faulted(data):
                data['bridge']['fault']='partial-write'
                for action in data['actions'].values(): action['allowed']=False
            await show(faulted)
            assert ids()==after and set(states().values())=={'unavailable'}
            status_override[0]=None
            fault[0]='partial-write'; await entry.runtime_data.async_refresh(); await hass.async_block_till_done()
            assert set(states().values())=={'unavailable'} and ids()==after
            fault[0]=None; await show()
            assert states()['room-a-mute']!='unavailable'
            # test_identify_and_system_power_never_entities: even when the bridge host lists them
            def identify_listed(data): data['actions']['identify']={'alias':False,'allowed':True,'function':'identify','source':None,'verified':True,'zone':None}
            await show(identify_listed)
            assert not any(k=='identify' or k.startswith('system-power') for k in buttons()) and ids()==after
            # test_button_unavailable_without_serial_open (schema 4): a closed serial port makes every button unavailable, no entity removed
            await show(lambda d: d['bridge'].update(serial_open=False))
            assert set(states().values())=={'unavailable'} and ids()==after
            await show()
            assert states()['room-a-mute']!='unavailable'
            # Extra (not in the plan list): a restart while the bridge host reports config null keeps every entity, unavailable and not restored
            await show(config_null)
            await hass.config_entries.async_reload(entry.entry_id); await hass.async_block_till_done()
            assert ids()==after
            assert ids()==after and set(states().values())=={'unavailable'}
            assert not any(hass.states.get(e).attributes.get('restored') for e in after.values())
            status_override[0]=None
            await entry.runtime_data.async_refresh(); await hass.async_block_till_done()
            assert ids()==after
            # Task 10b, schema 4 settings. bridge host config ops only: A-C enable is set_zone and never stored; Room D is set_zone then set_unverified.
            cfg.update(copy.deepcopy(FIXTURE_V4['config']))   # the fixture's own config, so each step below starts from the same state
            store=hass.data['bose_lifestyle']['store']
            await show()
            stored_before=copy.deepcopy(await store.async_load())
            bridge_calls.clear()
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='a',enabled=False)   # test_set_room_uses_set_zone_and_set_unverified_on_schema4
            assert not conn.errors and bridge_calls==[{'op':'set_zone','zone':'a','enabled':False}],(conn.errors,bridge_calls)
            assert await store.async_load()==stored_before                                            # A enable never written to the Store
            bridge_calls.clear()
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='d',enabled=True)
            assert not conn.errors and bridge_calls==[{'op':'set_zone','zone':'d','enabled':True},{'op':'set_unverified','zone':'d','allow':True}],(conn.errors,bridge_calls)
            bridge_calls.clear(); bridge_fail[0]=('set_unverified','uncertain')
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='d',enabled=False)
            assert conn.errors==['partial_failure'] and [c['op'] for c in bridge_calls]==['set_zone','set_unverified'],(conn.errors,bridge_calls)   # no retry
            bridge_calls.clear(); bridge_fail[0]=('set_zone','not_saved')
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='d',enabled=False)
            assert conn.errors==['not_saved'] and [c['op'] for c in bridge_calls]==['set_zone']          # bridge host code surfaced, not bridge_error
            bridge_fail[0]=None
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert 'd' not in [r['id'] for r in conn.results[0]['rooms']] and conn.results[0]['ir_target']['reason']=='zone-disabled'
            # test_ir_target_state_exposed (websocket part): reasons reach the layout, set_ir_zone is admin-only and forgets the mute state
            await show(lambda d: d['config']['ir'].update(zone='d'))
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert conn.results[0]['ir_target']=={'zone':'d','zone_name':'Room D','allowed':False,'reason':'zone-disabled'},conn.results[0]['ir_target']
            await show()
            bridge_calls.clear()
            conn=await ws(bws.ws_set_ir_zone,True,type='bose_lifestyle/set_ir_zone',zone='b')
            assert not conn.errors and bridge_calls==[{'op':'set_ir_zone','zone':'b'}] and 'forgotten' in conn.results[0]['message'] and 'ir_target' in conn.results[0],(conn.errors,bridge_calls)
            conn=await ws(bws.ws_set_ir_zone,False,type='bose_lifestyle/set_ir_zone',zone='b')
            assert conn.errors==['unauthorized'] and bridge_calls==[{'op':'set_ir_zone','zone':'b'}]
            conn=await ws(bws.ws_set_ir_zone,True,type='bose_lifestyle/set_ir_zone',zone='e')
            assert conn.errors==['invalid_request']
            # Volume steps per press: admin only, validated, written to the bridge host, shown by the layout
            bridge_calls.clear()
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert conn.results[0]['volume_steps']==1
            conn=await ws(bws.ws_set_volume_steps,True,type='bose_lifestyle/set_volume_steps',steps=3)
            assert not conn.errors and bridge_calls==[{'op':'set_volume_steps','steps':3}] and conn.results[0]=={'volume_steps':3},(conn.errors,bridge_calls,conn.results)
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert conn.results[0]['volume_steps']==3
            conn=await ws(bws.ws_set_volume_steps,False,type='bose_lifestyle/set_volume_steps',steps=2)
            assert conn.errors==['unauthorized'] and len(bridge_calls)==1
            for bad in (0,6,-1,True):
                conn=await ws(bws.ws_set_volume_steps,True,type='bose_lifestyle/set_volume_steps',steps=bad)
                assert conn.errors==['invalid_request'] and len(bridge_calls)==1,(bad,conn.errors)
            conn=await ws(bws.ws_set_volume_steps,True,type='bose_lifestyle/set_volume_steps',steps=1)
            assert not conn.errors and cfg.get('volume_steps') is None and bridge_calls[-1]=={'op':'set_volume_steps','steps':1}
            status_override[0]=None; await entry.runtime_data.async_refresh(); await hass.async_block_till_done()
            # Task 8, setup screen commands (admin only). Bridge host codes surface as-is.
            await show()
            # test_set_experimental_calls_set_unverified_for_every_zone: admin, schema 4; bridge codes surface as-is; invalid rooms never sent
            bridge_calls.clear()
            for zone in 'abcd':
                conn=await ws(bws.ws_set_experimental,True,type='bose_lifestyle/set_experimental',room=zone,allow=True)
                assert not conn.errors and 'setup' in conn.results[0],(zone,conn.errors)
            assert bridge_calls==[{'op':'set_unverified','zone':z,'allow':True} for z in 'abcd'],bridge_calls
            assert [r['experimental'] for r in (await ws(bws.ws_settings,True,type='bose_lifestyle/settings')).results[0]['rooms']]==[True]*4
            cfg.update(copy.deepcopy(FIXTURE_V4['config']))
            await show(lambda d: d['config']['unverified'].update(b=True))   # the bridge reports room B as allowed
            conn=await ws(bws.ws_settings,True,type='bose_lifestyle/settings')
            assert [r['experimental'] for r in conn.results[0]['rooms']]==[False,True,False,False],conn.results[0]['rooms']
            await show()
            bridge_calls.clear(); bridge_fail[0]=('set_unverified','not_saved')
            conn=await ws(bws.ws_set_experimental,True,type='bose_lifestyle/set_experimental',room='b',allow=False)
            assert conn.errors==['not_saved'] and [c['op'] for c in bridge_calls]==['set_unverified']
            bridge_fail[0]=None; bridge_calls.clear()
            conn=await ws(bws.ws_set_experimental,True,type='bose_lifestyle/set_experimental',room='e',allow=True)
            assert conn.errors==['invalid_request'] and not bridge_calls
            await show()
            # test_set_source_calls_set_source_op_and_surfaces_bridge_codes
            bridge_calls.clear()
            conn=await ws(bws.ws_set_source,True,type='bose_lifestyle/set_source',source='cd',connected=True)
            assert not conn.errors and bridge_calls==[{'op':'set_source','source':'cd','connected':True}],(conn.errors,bridge_calls)
            assert [s['label'] for s in conn.results[0]['setup']['sources']][:2]==['AM radio','FM radio']
            bridge_calls.clear(); bridge_fail[0]=('set_source','not_saved')
            conn=await ws(bws.ws_set_source,True,type='bose_lifestyle/set_source',source='tape',connected=True)
            assert conn.errors==['not_saved'] and [c['op'] for c in bridge_calls]==['set_source'],(conn.errors,bridge_calls)
            # test_setup_uncertain_commit_shown_not_retried
            bridge_calls.clear(); bridge_fail[0]=('set_source','uncertain')
            conn=await ws(bws.ws_set_source,True,type='bose_lifestyle/set_source',source='tape',connected=True)
            assert conn.errors==['uncertain'] and len(bridge_calls)==1,(conn.errors,bridge_calls)
            bridge_fail[0]=None; bridge_calls.clear()
            conn=await ws(bws.ws_set_source,True,type='bose_lifestyle/set_source',source='bogus',connected=True)
            assert conn.errors==['invalid_request'] and not bridge_calls
            # test_non_admin_cannot_call_setup_commands and test_non_admin_cannot_set_experimental (the set_experimental tuple below)
            for handler,msg in ((bws.ws_set_source,{'source':'cd','connected':True}),(bws.ws_set_room_name,{'room':'a','name':'Den'}),(bws.ws_set_experimental,{'room':'a','allow':True})):
                conn=await ws(handler,False,type='bose_lifestyle/x',**msg)
                assert conn.errors==['unauthorized'] and not conn.results and not bridge_calls,(handler,conn.errors)
            # test_blank_duplicate_or_overlong_name_rejected_never_saved (the Store is compared before and after)
            stored_before=copy.deepcopy(await store.async_load())
            for room,name,code in (('a','','invalid_name'),('a','   ','invalid_name'),('a','x'*41,'invalid_name'),('a','Lo\x01unge','invalid_name'),
                                   ('a','ROOM B','duplicate_name'),('c',' room d ','duplicate_name')):
                conn=await ws(bws.ws_set_room_name,True,type='bose_lifestyle/set_room_name',room=room,name=name)
                assert conn.errors==[code] and not conn.results,(name,conn.errors)
            assert await store.async_load()==stored_before and not bridge_calls
            conn=await ws(bws.ws_set_room_name,True,type='bose_lifestyle/set_room_name',room='a',name='x'*40)
            assert not conn.errors and conn.results[0]['rooms'][0]['name']=='x'*40
            conn=await ws(bws.ws_set_room_name,True,type='bose_lifestyle/set_room_name',room='e',name='Den')
            assert conn.errors==['invalid_request']
            await store.async_save(stored_before)
            # Task 8c: bridge computer nickname. Stored with the rooms, applied to the bridge device's name_by_user, default when cleared.
            def bridge_dev(): return dr.async_get(hass).async_get_device_by_identifier(('bose_lifestyle',entry.unique_id),entry.entry_id)
            def snapshot_ids():
                return (sorted((e.unique_id,e.entity_id) for e in er.async_entries_for_config_entry(registry,entry.entry_id)),
                        sorted(sorted(map(str,d.identifiers)) for d in dr.async_entries_for_config_entry(dr.async_get(hass),entry.entry_id)))
            ids_before=snapshot_ids()
            conn=await ws(bws.ws_set_bridge_name,False,type='bose_lifestyle/set_bridge_name',name='Den')   # test_non_admin_cannot_set_bridge_name
            assert conn.errors==['unauthorized'] and not conn.results and not bridge_calls
            assert await store.async_load()==stored_before
            conn=await ws(bws.ws_set_bridge_name,True,type='bose_lifestyle/set_bridge_name',name='  Den bridge  ')   # test_set_bridge_name_stores_and_sets_name_by_user
            assert not conn.errors and conn.results[0]['bridge_name']=='Den bridge',conn.errors
            assert (await store.async_load())['bridge_name']=='Den bridge' and (await store.async_load())['rooms']==stored_before['rooms']
            assert bridge_dev().name_by_user=='Den bridge' and bridge_dev().name=='Bose Controller'
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert conn.results[0]['bridge_name']=='Den bridge'
            conn=await ws(bws.ws_set_room,True,type='bose_lifestyle/set_room',room='a',name='Lounge')   # a room save keeps the nickname
            assert (await store.async_load())['bridge_name']=='Den bridge'
            ids_named=snapshot_ids()
            await hass.config_entries.async_reload(entry.entry_id); await hass.async_block_till_done()   # test_set_bridge_name_survives_reload
            assert bridge_dev().name_by_user=='Den bridge' and (await store.async_load())['bridge_name']=='Den bridge'
            assert snapshot_ids()==ids_named   # unique IDs, entity_ids and device identifiers unchanged
            dr.async_get(hass).async_update_device(bridge_dev().id,name_by_user=None)   # the device lost its nickname (recreated): setup puts the stored one back
            await hass.config_entries.async_reload(entry.entry_id); await hass.async_block_till_done()
            assert bridge_dev().name_by_user=='Den bridge' and snapshot_ids()==ids_named
            saved=copy.deepcopy(await store.async_load())
            for name in ('','   ','x'*41,'Lo\x01unge'):   # test_blank_overlong_control_char_bridge_name_rejected_never_saved
                conn=await ws(bws.ws_set_bridge_name,True,type='bose_lifestyle/set_bridge_name',name=name)
                assert conn.errors==['invalid_name'] and not conn.results,(name,conn.errors)
            assert await store.async_load()==saved and bridge_dev().name_by_user=='Den bridge'
            conn=await ws(bws.ws_set_bridge_name,True,type='bose_lifestyle/set_bridge_name',name=None)   # test_clear_bridge_name_restores_default
            assert not conn.errors and conn.results[0]['bridge_name']=='Bose Controller'
            assert 'bridge_name' not in await store.async_load() and bridge_dev().name_by_user is None
            conn=await ws(bws.ws_set_bridge_name,True,type='bose_lifestyle/set_bridge_name',name='Room A')   # test_bridge_name_not_required_unique_vs_rooms
            assert not conn.errors and conn.results[0]['bridge_name']=='Room A'
            conn=await ws(bws.ws_set_bridge_name,True,type='bose_lifestyle/set_bridge_name')   # omitted name also clears
            assert conn.results[0]['bridge_name']=='Bose Controller' and snapshot_ids()==ids_before
            await store.async_save(stored_before)
            # test_fresh_install_shows_finish_setup_for_admin_and_neutral_message_for_others (layout for everyone, settings for administrators)
            def fresh(data):
                for zone in 'abcd': data['config']['zones'][zone]['enabled']=False
            await show(fresh)
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert conn.results[0]['needs_setup'] is True and not conn.errors
            conn=await ws(bws.ws_settings,True,type='bose_lifestyle/settings')
            assert conn.results[0]['setup']['needs_setup'] is True
            await show()
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert conn.results[0]['needs_setup'] is False
            # test_daily_controls_hide_unconnected_sources: only connected inputs reach the daily layout
            room_a=conn.results[0]['rooms'][0]['buttons']
            assert 'tv' in room_a and all(('source-'+src in room_a)==(cfg['sources'][src] is True) for src in ('cd','fm','tape'))   # connected inputs only
            # Task 10g: IR receiver label (admin only, Store only, never a bridge command)
            label_before=copy.deepcopy(await store.async_load()); calls_mark=list(bridge_calls)
            conn=await ws(bws.ws_set_ir_receiver_label,False,type='bose_lifestyle/set_ir_receiver_label',label='Den')   # test_non_admin_cannot_set_ir_receiver_label
            assert conn.errors==['unauthorized'] and not conn.results and bridge_calls==calls_mark and await store.async_load()==label_before
            conn=await ws(bws.ws_set_ir_receiver_label,True,type='bose_lifestyle/set_ir_receiver_label',label='  Kitchen  ')
            assert not conn.errors and conn.results[0]['ir_receiver_label']=='Kitchen' and (await store.async_load())['ir_receiver_label']=='Kitchen'
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')
            assert conn.results[0]['ir_receiver_label']=='Kitchen' and not conn.errors
            conn=await ws(bws.ws_set_ir_receiver_label,True,type='bose_lifestyle/set_ir_receiver_label',label='x'*41)
            assert conn.errors==['invalid_name'] and (await store.async_load())['ir_receiver_label']=='Kitchen' and bridge_calls==calls_mark
            conn=await ws(bws.ws_set_ir_receiver_label,True,type='bose_lifestyle/set_ir_receiver_label',label=None)   # null clears
            assert not conn.errors and 'ir_receiver_label' not in await store.async_load()
            conn=await ws(bws.ws_set_ir_receiver_label,True,type='bose_lifestyle/set_ir_receiver_label',label='Den')
            conn=await ws(bws.ws_set_ir_receiver_label,True,type='bose_lifestyle/set_ir_receiver_label')   # an omitted label clears too
            assert not conn.errors and 'ir_receiver_label' not in await store.async_load() and bridge_calls==calls_mark
            conn=await ws(bws.ws_settings,True,type='bose_lifestyle/settings')
            assert conn.results[0]['reason'] is None and conn.results[0]['bridge']['config']=='ok' and conn.results[0]['ir_receiver_label'] is None
            # Task 10d: input nicknames and icons (admin only, Store only, never a bridge command)
            inputs_before=copy.deepcopy(await store.async_load()); calls_mark=list(bridge_calls)
            conn=await ws(bws.ws_set_input,False,type='bose_lifestyle/set_input',source='cd',name='Den')   # test_non_admin_cannot_set_input
            assert conn.errors==['unauthorized'] and not conn.results and bridge_calls==calls_mark and await store.async_load()==inputs_before
            conn=await ws(bws.ws_set_input,True,type='bose_lifestyle/set_input',source='cd',name='  Den CD  ',icon='speaker')   # test_set_input_stores_name_and_icon_per_source
            assert not conn.errors and conn.results[0]['inputs']['cd']=={'name':'Den CD','icon':'speaker'},conn.errors
            stored=await store.async_load()
            assert stored['inputs']=={'cd':{'name':'Den CD','icon':'speaker'}} and stored['rooms']==inputs_before['rooms'] and stored.get('bridge_name')==inputs_before.get('bridge_name')
            conn=await ws(bws.ws_set_input,True,type='bose_lifestyle/set_input',source='cd',icon='tv')   # an omitted name keeps the nickname
            assert conn.results[0]['inputs']['cd']=={'name':'Den CD','icon':'tv'}
            conn=await ws(bws.ws_set_input,True,type='bose_lifestyle/set_input',source='cd',name=None)   # Use default keeps the icon
            assert conn.results[0]['inputs']['cd']=={'name':'CD player','icon':'tv'}
            for kwargs,code in (({'name':''},'invalid_name'),({'name':'   '},'invalid_name'),({'name':'x'*31},'invalid_name'),({'name':'Lo\x01unge'},'invalid_name'),
                                ({'name':5},'invalid_name'),({'icon':'bogus'},'invalid_icon'),({'icon':'TV'},'invalid_icon')):   # test_set_input_rejects_blank_overlong_control_chars_and_unknown_icon
                before=copy.deepcopy(await store.async_load())
                conn=await ws(bws.ws_set_input,True,type='bose_lifestyle/set_input',source='cd',**kwargs)
                assert conn.errors==[code] and not conn.results and await store.async_load()==before,(kwargs,conn.errors)
            conn=await ws(bws.ws_set_input,True,type='bose_lifestyle/set_input',source='e',name='Den')
            assert conn.errors==['invalid_request'] and bridge_calls==calls_mark
            conn=await ws(bws.ws_set_input,True,type='bose_lifestyle/set_input',source='cd',name=None,icon=None)   # test_set_input_null_resets_to_default
            assert not conn.errors and 'cd' not in (await store.async_load()).get('inputs',{}) and conn.results[0]['inputs']['cd']=={'name':'CD player','icon':'disc'}
            conn=await ws(bws.ws_set_input,True,type='bose_lifestyle/set_input',source='video-1',name='Den TV')
            conn=await ws(bws.ws_layout,False,type='bose_lifestyle/layout')   # test_layout_uses_nickname_and_icon_for_every_room
            assert conn.results[0]['inputs']['video-1']=={'name':'Den TV','icon':'tv'} and conn.results[0]['rooms'] and all('tv' in r['buttons'] for r in conn.results[0]['rooms'])
            assert all(r['buttons']['tv'] for r in conn.results[0]['rooms'])
            await store.async_save(inputs_before)
        try: await entry.runtime_data.client.call('room-d-source-bogus')   # a name no status listed never reaches the bridge
        except BridgeError as error: assert str(error)=='invalid_action'
        else: raise AssertionError('unlisted client action escaped the gate')
        from custom_components.bose_lifestyle.diagnostics import async_get_config_entry_diagnostics
        with patch.object(Client,'call',fake_call):
            with_profile=copy.deepcopy(entry.runtime_data.data)
            with_profile['ir']['profiles']={'a'*32:{'name':'Den Remote','model':'ir-remote','active':True,'mappings':{'volume-up':{'code':460551,'enabled':True},'volume-down':{'code':None,'enabled':False},'mute':{'code':None,'enabled':False}}}}
            entry.runtime_data.data=with_profile
            diag=await async_get_config_entry_diagnostics(hass,entry)
            text=json.dumps(diag)
            assert 'Den Remote' in text and '460551' not in text   # remote names kept, learned IR codes dropped
            assert set(diag)=={'entry','status','rooms','events'} and diag['events']==events_data
            assert 'bridge.example.invalid' not in text and 'bose_ed25519' not in text and '"code"' not in text, text[:600]
            assert diag['entry']['host']=='**REDACTED**' and 'profiles' in diag['status']['ir']
            assert [r['id'] for r in diag['rooms']]==['a','b','c','d']
        with patch.object(Client,'call',lambda self,action,payload=None: (_ for _ in ()).throw(BridgeError('cannot_connect'))):
            assert (await async_get_config_entry_diagnostics(hass,entry))['events']==[]
        await hass.async_stop()
    print('HA 2026.9.4: A/B/C devices, live B/C entities, guarded Room D, native remote flows and stale availability passed')

def test_no_m1_jargon_in_user_text():
    """The model shorthand is not in any text a person sees (card, strings, device model, admin log); reply strings in commands.py are bridge-side."""
    token = ''.join(['M', '1'])
    pattern = re.compile(r'\b' + token + r'\b')
    root = Path(__file__).resolve().parent / 'custom_components' / 'bose_lifestyle'
    for name in ('frontend/bose-control-card.js', 'strings.json', 'translations/en.json', '__init__.py', 'entity.py'):
        for number, line in enumerate((root / name).read_text(encoding='utf-8').splitlines(), 1):
            assert not pattern.search(line), f'{name}:{number}'

def test_no_m1_jargon_in_any_user_string():
    """Exceptions and the card's message table (the user-facing copy of every error) never use the model shorthand."""
    import json
    root = Path(__file__).resolve().parent / 'custom_components' / 'bose_lifestyle'
    token = re.compile(r'\bM1\b')
    exceptions = json.loads((root / 'strings.json').read_text(encoding='utf-8'))['exceptions']
    for key, item in exceptions.items(): assert not token.search(item['message']), key
    card = (root / 'frontend' / 'bose-control-card.js').read_text(encoding='utf-8')
    for number, line in enumerate(card.splitlines(), 1): assert not token.search(line), f'card:{number}'

if __name__=='__main__':
    test_no_m1_jargon_in_user_text()
    test_no_m1_jargon_in_any_user_string()
    asyncio.run(run())
