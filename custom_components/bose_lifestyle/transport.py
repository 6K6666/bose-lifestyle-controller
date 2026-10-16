"""SSH transport: one bounded process per call, never retried. Argv, timeout and caps are pinned by test_bose_transport.py."""
import asyncio
import re
from pathlib import Path
from typing import Mapping, NamedTuple, Protocol, Tuple
from homeassistant.exceptions import HomeAssistantError
from .protocol import MAX_REPLY

HOST_PATTERN = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}')
USER_PATTERN = re.compile(r'[a-z_][a-z0-9_-]{0,31}')
TRANSLATED_ERRORS = frozenset({'not_allowed', 'uncertain', 'not_sent', 'connection_lost', 'fault_paused', 'config_uncertain', 'refused_by_unit', 'invalid_response', 'bridge_unavailable'})   # strings.json "exceptions"

class BridgeError(HomeAssistantError):
    def __init__(self, code):
        if code in TRANSLATED_ERRORS: super().__init__(code, translation_domain='bose_lifestyle', translation_key=code)
        else: super().__init__(code)

class TransportResult(NamedTuple):
    returncode: int
    stdout: bytes
    unclear: bool = False   # the connection was made but the reply is incomplete (timeout or I/O error): the command may have been sent

class Transport(Protocol):
    async def call(self, action: str, body: bytes) -> TransportResult: ...

class SshTransport:
    def __init__(self, config_dir: Path, host: str, user: str, port: int):
        self.host, self.user, self.port = host, user, port
        ssh = Path(config_dir) / '.ssh'
        self.key = str(ssh / 'bose_ed25519')
        self.known_hosts = str(ssh / 'bose_known_hosts')
        self.control_path = str(ssh / 'bose-%C')

    def argv(self, action):
        return ['ssh','-T','-i',self.key,
                '-o','IdentitiesOnly=yes','-o','BatchMode=yes',
                '-o','StrictHostKeyChecking=yes','-o',f'UserKnownHostsFile={self.known_hosts}',
                '-o','ConnectTimeout=3','-o','ConnectionAttempts=1',
                '-o','ControlMaster=auto','-o','ControlPersist=60','-o',f'ControlPath={self.control_path}',
                '-p',str(self.port),f"{self.user}@{self.host}",action]

    async def call(self, action, body):
        args = self.argv(action)
        process = None
        async def read(stream, limit):
            result = bytearray()
            while chunk := await stream.read(4096):
                result.extend(chunk)
                if len(result)>limit: raise BridgeError('oversized_response')
            return bytes(result)
        try:
            async with asyncio.timeout(5):
                process = await asyncio.create_subprocess_exec(*args,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                process.stdin.write(body)
                await process.stdin.drain()
                process.stdin.close()
                output, _ = await asyncio.gather(read(process.stdout,MAX_REPLY),read(process.stderr,4096))
                await process.wait()
            return TransportResult(process.returncode, output)
        except TimeoutError:
            return TransportResult(-1, b'', unclear=True)
        except OSError:
            if process is None: raise   # the process never started: nothing can have reached the bridge
            return TransportResult(-1, b'', unclear=True)
        finally:
            if process and process.returncode is None:
                process.kill()
                await process.wait()

class FakeTransport:
    """Test double: fixed (returncode, stdout) per action; records every call."""
    def __init__(self, replies: Mapping[str, Tuple[int, bytes]]):
        self.replies = dict(replies)
        self.calls = []

    async def call(self, action, body):
        self.calls.append((action, body))
        returncode, stdout = self.replies[action]
        return TransportResult(returncode, stdout)
