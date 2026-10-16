"""Native admin-only bridge and remote subentry setup."""
import asyncio
from contextlib import suppress
from pathlib import Path
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigSubentryFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers import device_registry as dr
from . import pairing
from .client import Client, BridgeError
from .transport import HOST_PATTERN, USER_PATTERN
from .protocol import KEYS
from . import DOMAIN, initial_groups, group, remote_profiles

MODEL='ir-remote'   # the only remote model; the free-text name is the remote's nickname
NAME_LIMIT=40
ACTIONS={'volume-up':'Volume up','volume-down':'Volume down','mute':'Mute'}   # the target room is chosen in settings and resolved at press time

def choice(options):
    return selector.SelectSelector(selector.SelectSelectorConfig(options=[{'value':k,'label':v} for k,v in options.items()],mode=selector.SelectSelectorMode.LIST))

class BoseFlow(ConfigFlow,domain=DOMAIN):
    VERSION=2
    def __init__(self):
        self.data=None; self.line=None; self.fingerprint=None
    @staticmethod
    @callback
    def async_get_options_flow(config_entry): return BoseOptionsFlow()
    @classmethod
    @callback
    def async_get_supported_subentry_types(cls,config_entry): return {'controller':GroupFlow,'zones':GroupFlow,'remotes':RemoteFlow}

    @property
    def config_dir(self): return Path(self.hass.config.config_dir)

    async def async_step_user(self,user_input=None):
        problems=await self.hass.async_add_executor_job(pairing.environment_problems,self.config_dir)
        if problems: return self.async_abort(reason=problems[0])
        return await self.async_step_key()

    async def async_step_key(self,user_input=None):
        if user_input is not None: return self.show_host()
        try: public_key=await self.hass.async_add_executor_job(_public_key,self.config_dir)
        except pairing.PairingError as error: return self.async_abort(reason=error.code)
        command=f"sh install-bridge.sh --public-key '{public_key}' --user <user> --serial-device <path-to-serial-by-id>"
        return self.async_show_form(step_id='key',description_placeholders={'command':command})

    def show_host(self,errors=None):
        return self.async_show_form(step_id='host',data_schema=vol.Schema({vol.Required('host'):str,vol.Required('user',default='bose'):str,
            vol.Required('port',default=22):vol.Coerce(int)}),errors=errors or {})

    async def async_step_host(self,user_input=None):
        errors={}
        if user_input is not None:
            host,user,port=user_input['host'],user_input['user'],user_input['port']
            if not HOST_PATTERN.fullmatch(host): errors['host']='invalid_host'
            if not USER_PATTERN.fullmatch(user): errors['user']='invalid_user'
            if type(port) is not int or not 1<=port<=65535: errors['port']='invalid_port'
            if not errors:
                try: self.line=await self.hass.async_add_executor_job(pairing.scan_host_key,host,port)
                except pairing.PairingError as error: errors['base']=error.code
                else:
                    self.data={'host':host,'user':user,'port':port}
                    self.fingerprint=pairing.fingerprint_of(self.line)
                    return self.show_fingerprint()
        return self.show_host(errors)

    def show_fingerprint(self,errors=None):
        return self.async_show_form(step_id='fingerprint',data_schema=vol.Schema({vol.Required('fingerprint'):str}),errors=errors or {},
            description_placeholders={'fingerprint':self.fingerprint})

    async def async_step_fingerprint(self,user_input=None):
        errors={}
        if user_input is not None:
            try: await self.hass.async_add_executor_job(pairing.pin_host_key,self.line,self.config_dir/'.ssh'/pairing.KNOWN_HOSTS_NAME,user_input['fingerprint'].strip())
            except pairing.FingerprintMismatch: return self.async_abort(reason='fingerprint_mismatch')
            except pairing.PairingError as error: errors['base']=error.code
            else:
                try: status=await Client(self.data,config_dir=self.config_dir).call('status')
                except BridgeError as error: errors['base']='forced_command_refused' if str(error)=='invalid_response' else 'cannot_connect'
                else:
                    await self.async_set_unique_id(status['bridge_id'])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(title='Bose Controller',data=self.data,
                        subentries=initial_groups({rid: {'remote_id': rid} for rid in status['ir']['profiles']}))
        return self.show_fingerprint(errors)

class BoseOptionsFlow(OptionsFlow):
    async def async_step_init(self,user_input=None):
        if user_input is not None:
            message=user_input.get('fault_message','').strip()
            if len(message)>160 or any(ord(char)<32 or ord(char)==127 for char in message):
                return self.async_show_form(step_id='init',data_schema=self._schema(message),errors={'base':'invalid_fault_message'})
            return self.async_create_entry(title='',data={'fault_message':message})
        return self.async_show_form(step_id='init',data_schema=self._schema(self.config_entry.options.get('fault_message','')))

    @staticmethod
    def _schema(default):
        return vol.Schema({vol.Optional('fault_message',default=default):vol.All(str,vol.Length(max=160))})

def _public_key(config_dir):
    return pairing.existing_public_key(config_dir) or pairing.generate_keypair(config_dir)

class GroupFlow(ConfigSubentryFlow):
    async def async_step_user(self,user_input=None): return self.async_abort(reason='managed_in_card')
    async def async_step_reconfigure(self,user_input=None): return self.async_abort(reason='managed_in_card')

class RemoteFlow(ConfigSubentryFlow):
    def __init__(self):
        self.sid=None; self.state=None; self.task=None; self.error=None; self.sub=None; self.imports={}
        self.rid=self.rname=None; self.check_keys=[]; self.check_codes={}

    @property
    def client(self): return self._get_entry().runtime_data.client

    async def async_step_user(self,user_input=None):
        try:
            coordinator=self._get_entry().runtime_data
            if not coordinator.fresh: raise BridgeError('cannot_connect')
            status=coordinator.data
            registered=remote_profiles(self._get_entry())
            self.imports={rid:p for rid,p in status['ir']['profiles'].items() if rid not in registered}
        except BridgeError: return self.async_abort(reason='cannot_connect')
        if user_input:
            if user_input['profile']!='new':
                rid=user_input['profile']; p=self.imports.get(rid)
                if not p: return self.async_abort(reason='unknown_remote')
                self.rid,self.rname=rid,p['name']
                return self.finish()
            return await self.async_step_name()
        if not self.imports: return await self.async_step_name()
        return self.async_show_form(step_id='user',data_schema=vol.Schema({vol.Required('profile',default='new'):choice({'new':'Set up a new remote',**{rid:p['name'] for rid,p in self.imports.items()}})}))

    async def async_step_name(self,user_input=None):
        errors={}
        if user_input:
            name=user_input['name'].strip()
            if not 1<=len(name)<=NAME_LIMIT: errors['base']='name_length'
            else:
                try:
                    self.state=await self.client.manage('begin',name=name,model=MODEL)
                    self.sid=self.state['session_id']
                    return await self.async_step_prepare()
                except BridgeError as e: errors['base']=str(e) if str(e) in ('busy','invalid_request','remote_limit') else 'cannot_connect'
        return self.async_show_form(step_id='name',data_schema=vol.Schema({vol.Required('name'):str}),errors=errors)

    async def async_step_prepare(self,user_input=None):
        if user_input:
            try:
                if user_input['choice']=='skip':
                    self.state=await self.client.manage('choose',session_id=self.sid,choice='skip')
                    return await self.advance()
                self.state=await self.client.manage('capture',session_id=self.sid)
                self.task=None
                return await self.async_step_wait()
            except BridgeError: return self.async_abort(reason='cannot_connect')
        return self.async_show_form(step_id='prepare',data_schema=vol.Schema({vol.Required('choice',default='listen'):choice({'listen':'Listen for three presses','skip':'Skip this button'})}),description_placeholders=self.instructions())

    def instructions(self):
        return {'name':self.state['name'],'action':ACTIONS.get(self.state['key'],''),'step':str(self.state['step']),'steps':str(self.state['steps']),'verified':str(self.state['verified'])}

    async def listen(self):
        try:
            while True:
                previous=self.state['verified']
                self.state=await self.client.manage('session',session_id=self.sid)
                self.async_update_progress(self.state['verified']/3)
                if self.state['verified']!=previous: self.async_notify_flow_changed()
                if self.state['phase']!='waiting': return
                await asyncio.sleep(.35)
        except BridgeError as e: self.error=str(e)

    async def async_step_wait(self,user_input=None):
        if self.task is None:
            self.task=self.hass.async_create_task(self.listen(),'Bose remote capture')
        if self.task.done():
            return self.async_show_progress_done(next_step_id='detected')
        return self.async_show_progress(step_id='wait',progress_action='capture',description_placeholders=self.instructions(),progress_task=self.task)

    async def async_step_detected(self,user_input=None):
        if self.error: return self.async_abort(reason='cannot_connect')
        errors={}
        if user_input:
            try:
                self.state=await self.client.manage('choose',session_id=self.sid,choice=user_input['choice'])
                if user_input['choice']=='retry':
                    self.task=None
                    return await self.async_step_wait()
                return await self.advance()
            except BridgeError as e: errors['base']=str(e) if str(e) in ('no_capture','conflict') else 'cannot_connect'
        options={'retry':'Retry','skip':'Skip'}
        if self.state['phase']=='captured': options={'keep':'Keep',**options}
        notes={'captured':'Three matching presses verified.','timeout':'No complete set of three matching presses arrived within 30 seconds.',
               'conflict':'This code is already assigned to a different action. Retry or skip.',
               'inconsistent':'The presses produced different codes. Retry or skip.'}
        if self.state['shared']: notes[self.state['phase']]+=' This code is shared with another remote; identical IR signals cannot identify the physical remote.'
        return self.async_show_form(step_id='detected',data_schema=vol.Schema({vol.Required('choice',default=next(iter(options))):choice(options)}),errors=errors,
            description_placeholders={**self.instructions(),'note':notes[self.state['phase']]})

    async def advance(self):
        if self.state['phase']=='review': return await self.async_step_review()
        return await self.async_step_prepare()

    async def async_step_review(self,user_input=None):
        if user_input is not None:
            try:
                result=await self.client.manage('commit',session_id=self.sid)
                self.sid=None
                await self._get_entry().runtime_data.async_request_refresh()
                self.rid,self.rname=result['remote_id'],self.state['name']
                self.check_keys=[k for k in KEYS if self.state['mappings'][k]['code'] is not None]
                self.check_codes={k:self.state['mappings'][k]['code'] for k in self.check_keys}
                if not self.check_keys: return self.finish()
                return await self.async_step_check()
            except BridgeError: return self.async_abort(reason='cannot_connect')
        lines=[f"{ACTIONS[k]}: {'Learned' if m['code'] is not None else 'Skipped'}" for k,m in self.state['mappings'].items()]
        return self.async_show_form(step_id='review',data_schema=vol.Schema({}),description_placeholders={'name':self.state['name'],'mappings':'\n\n'.join(lines)})

    def finish(self):
        entry=self._get_entry()
        sub=group(entry,'remotes')
        profiles=dict(sub.data['profiles'])
        profiles.setdefault(self.rid,{'remote_id':self.rid})
        return self.async_update_and_abort(entry,sub,data={**sub.data,'profiles':profiles},reason='reconfigure_successful')

    async def async_step_check(self,user_input=None):
        # Shown before any session starts: the pause warning is visible while no learning session exists.
        if user_input is not None:
            try:
                self.state=await self.client.manage('begin',name=self.rname,model=MODEL,remote_id=self.rid,keys=self.check_keys)
                self.sid=self.state['session_id']
                self.task=None
                return await self.async_step_check_wait()
            except BridgeError: return self.async_abort(reason='cannot_connect')
        return self.async_show_form(step_id='check',data_schema=vol.Schema({}))

    async def check_listen(self):
        # One press of the expected button ticks it; any press (right or wrong) ends the wait so the form can decide.
        try:
            self.state=await self.client.manage('capture',session_id=self.sid)
            while True:
                self.state=await self.client.manage('session',session_id=self.sid)
                if self.state['verified']>=1 or self.state['phase']!='waiting': return
                await asyncio.sleep(.35)
        except BridgeError as e: self.error=str(e)

    async def async_step_check_wait(self,user_input=None):
        if self.task is None:
            self.task=self.hass.async_create_task(self.check_listen(),'Bose ready check')
        if self.task.done():
            return self.async_show_progress_done(next_step_id='check_detected')
        return self.async_show_progress(step_id='check_wait',progress_action='check',description_placeholders=self.check_instructions(),progress_task=self.task)

    def check_instructions(self):
        key=self.state['key']
        return {'name':self.rname,'action':ACTIONS.get(key,''),'step':str(self.state['step']),'steps':str(self.state['steps'])}

    async def async_step_check_detected(self,user_input=None):
        if self.error: return self.async_abort(reason='cannot_connect')
        key=self.state['key']
        matched=self.state['verified']>=1 and self.state['candidate']==self.check_codes[key]
        if user_input is not None:
            try:
                if not matched:
                    self.task=None
                    return await self.async_step_check_wait()
                self.state=await self.client.manage('choose',session_id=self.sid,choice='skip')   # staged only: the saved profile is never committed again
                if self.state['phase']!='review':
                    self.task=None
                    return await self.async_step_check_wait()
                await self.client.manage('cancel',session_id=self.sid)
                self.sid=None
                return self.finish()
            except BridgeError: return self.async_abort(reason='cannot_connect')
        if matched: note='Press verified.'
        elif self.state['phase']=='timeout': note='No press arrived within 30 seconds. Select Continue, then press the button once.'
        else: note='That press did not match this button. Select Continue, then press the button once.'
        return self.async_show_form(step_id='check_detected',data_schema=vol.Schema({}),
            description_placeholders={**self.check_instructions(),'note':note})

    async def async_step_reconfigure(self,user_input=None):
        self.sub=self._get_reconfigure_subentry()
        if self.rid is None:
            if not user_input:
                profiles=self._get_entry().runtime_data.data['ir']['profiles']
                return self.async_show_form(step_id='reconfigure',data_schema=vol.Schema({vol.Required('remote_id'):choice({rid:p['name'] for rid,p in profiles.items() if rid in self.sub.data['profiles']})}))
            self.rid=user_input.get('remote_id')
            if self.rid not in self.sub.data['profiles']: return self.async_abort(reason='unknown_remote')
            user_input=None
        if user_input:
            return await getattr(self,'async_step_'+user_input['operation'])()
        return self.async_show_form(step_id='reconfigure',data_schema=vol.Schema({vol.Required('operation',default='relearn'):choice({'relearn':'Relearn a button','mapping':'Enable, disable, or remove a mapping','rename':'Rename remote','delete':'Delete remote profile from the bridge computer'})}))

    async def get_profile(self):
        coordinator=self._get_entry().runtime_data
        if not coordinator.fresh: raise BridgeError('cannot_connect')
        data=coordinator.data
        profile=data['ir']['profiles'].get(self.rid)
        if not profile: raise BridgeError('unknown_remote')
        return profile

    async def async_step_relearn(self,user_input=None):
        if user_input:
            try:
                p=await self.get_profile()
                self.state=await self.client.manage('begin',name=p['name'],model=MODEL,remote_id=self.rid,keys=[user_input['key']])
                self.sid=self.state['session_id']
                return await self.async_step_prepare()
            except BridgeError: return self.async_abort(reason='cannot_connect')
        return self.async_show_form(step_id='relearn',data_schema=vol.Schema({vol.Required('key'):choice(ACTIONS)}))

    async def async_step_mapping(self,user_input=None):
        errors={}
        if user_input:
            try:
                fields={'remote_id':self.rid,'key':user_input['key']}
                if user_input['operation']=='remove': fields['remove']=True
                else: fields['enabled']=user_input['operation']=='enable'
                await self.client.manage('update',**fields)
                await self._get_entry().runtime_data.async_request_refresh()
                return self.async_abort(reason='reconfigure_successful')
            except BridgeError as e: errors['base']='no_mapping' if str(e)=='no_mapping' else 'cannot_connect'
        try: p=await self.get_profile()
        except BridgeError: return self.async_abort(reason='unknown_remote')
        return self.async_show_form(step_id='mapping',data_schema=vol.Schema({vol.Required('key'):choice(ACTIONS),vol.Required('operation',default='disable'):choice({'enable':'Enable one button for physical acceptance','disable':'Disable button','remove':'Remove mapping'})}),errors=errors,description_placeholders={'mappings':'\n\n'.join(f"{ACTIONS[k]}: {'Learned' if m['code'] is not None else 'Skipped'}; {'enabled' if m['enabled'] else 'disabled'}" for k,m in p['mappings'].items())})

    async def async_step_rename(self,user_input=None):
        errors={}
        if user_input:
            if not 1<=len(user_input['name'].strip())<=NAME_LIMIT: errors['base']='name_length'
            else:
                try:
                    await self.client.manage('update',remote_id=self.rid,name=user_input['name'])
                    await self._get_entry().runtime_data.async_request_refresh()
                    devices=dr.async_get(self.hass)
                    device=devices.async_get_device_by_identifier((DOMAIN,self.rid),self._get_entry().entry_id)
                    if device: devices.async_update_device(device.id,name=user_input['name'].strip())
                    return self.async_update_and_abort(self._get_entry(),self.sub,reason='reconfigure_successful')
                except BridgeError: errors['base']='invalid_request'
        try: p=await self.get_profile()
        except BridgeError: return self.async_abort(reason='unknown_remote')
        return self.async_show_form(step_id='rename',data_schema=vol.Schema({vol.Required('name',default=p['name']):str}),errors=errors)

    async def async_step_delete(self,user_input=None):
        if user_input is not None:
            try: await self.client.manage('delete',remote_id=self.rid)
            except BridgeError: return self.async_abort(reason='cannot_connect')
            entry=self._get_entry()
            profiles={rid:data for rid,data in self.sub.data['profiles'].items() if rid!=self.rid}
            devices=dr.async_get(self.hass)
            device=devices.async_get_device_by_identifier((DOMAIN,self.rid),entry.entry_id)
            if device: devices.async_remove_device(device.id)
            return self.async_update_and_abort(entry,self.sub,data={**self.sub.data,'profiles':profiles},reason='deleted')
        return self.async_show_form(step_id='delete',data_schema=vol.Schema({}))

    @callback
    def async_remove(self):
        if self.task and not self.task.done(): self.task.cancel()
        if self.sid:
            sid=self.sid; self.sid=None
            async def cancel():
                with suppress(BridgeError): await self.client.manage('cancel',session_id=sid)
            self.hass.async_create_task(cancel(),'Cancel Bose learning')
