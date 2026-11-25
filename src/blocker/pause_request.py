"""Short-lived, service-owned Pause requests over local Windows named pipes."""
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass

from blocker.hosts import normalize_host

WAIT_SEC = 0.5
POLL_SEC = 0.02
MAX_MESSAGE = 65536
STATUSES = {"paused", "attempted", "unavailable"}
_NAME = r"\\.\pipe\Lockdown.SitePause.v1."
# Specific rights: generic write would also let a client create pipe instances.
_CLIENT_ACCESS = 0x120183  # SYNCHRONIZE, READ_CONTROL, read/write attributes and data
_SYSTEM, _ADMINS = "S-1-5-18", "S-1-5-32-544"


@dataclass(frozen=True)
class Request:
    generation: str
    hosts: frozenset[str]
    deadline: float

    def encode(self):
        return _encode({"generation": self.generation, "hosts": sorted(self.hosts), "deadline": self.deadline})

    @classmethod
    def decode(cls, raw, now):
        value = _decode(raw)
        try:
            generation, hosts, deadline = value["generation"], value["hosts"], float(value["deadline"])
            if (len(generation) == 32 and len(bytes.fromhex(generation)) == 16 and now < deadline <= now + WAIT_SEC
                    and isinstance(hosts, list) and hosts and all(normalize_host(h) == h for h in hosts)):
                return cls(generation, frozenset(hosts), deadline)
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        return None


@dataclass(frozen=True)
class Receipt:
    generation: str
    outcomes: tuple[str, ...]


def _encode(value):
    return json.dumps(value, separators=(",", ":")).encode("ascii")


def _decode(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError, RecursionError):
        return {}


def ack(generation, status):
    return _encode({"generation": generation, "status": status})


def ack_status(raw, generation):
    reply = _decode(raw)
    status = reply.get("status")
    if reply.get("generation") == generation and isinstance(status, str) and status in STATUSES:
        return status
    return None


def request(hosts, *, server=None, clock=time.monotonic, sleep=time.sleep, stop=None):
    """Offer one generation to every in-scope session on one absolute deadline."""
    msg = Request(secrets.token_hex(16), frozenset(hosts), clock() + WAIT_SEC)
    payload = msg.encode()
    if len(payload) > MAX_MESSAGE:
        return Receipt(msg.generation, ("scope_too_large",))
    endpoints, outcomes = {}, []
    try:
        if server is None:
            if sys.platform != "win32":
                return Receipt(msg.generation, ("no_session",))
            server = _WindowsPipes()
        for pipe in server.offer(msg.deadline):
            endpoints[pipe] = False
        while endpoints and clock() < msg.deadline and not (stop and stop.is_set()):
            for pipe, sent in tuple(endpoints.items()):
                if clock() >= msg.deadline or (stop and stop.is_set()):
                    break
                if not sent:
                    if not pipe.connected():
                        continue
                    if not pipe.send(payload):
                        continue
                    endpoints[pipe] = True
                if (raw := pipe.receive()) is not None:
                    status = ack_status(raw, msg.generation)
                    if clock() >= msg.deadline or (stop and stop.is_set()):
                        break
                    if status:
                        outcomes.append(status)
                        del endpoints[pipe]
                        pipe.close()
                    elif "invalid_ack" not in outcomes:
                        outcomes.append("invalid_ack")
            if endpoints:
                sleep(max(0, min(POLL_SEC, msg.deadline - clock())))
        outcomes.extend(("ack_timeout" if sent else "no_agent") for sent in endpoints.values())
    finally:
        for pipe in endpoints:
            pipe.close()
    return Receipt(msg.generation, tuple(outcomes) or ("no_session",))


class _Pipe:
    def __init__(self, native, handle, session=None):
        self.native, self.handle, self.session = native, handle, session

    def connected(self):
        try:
            result = self.native.pipe.ConnectNamedPipe(self.handle, None)
        except self.native.error as error:
            result = error.winerror
        if result not in (0, 535):  # ERROR_PIPE_CONNECTED: the client already called CreateFile
            return False
        return self.native.client_session(self.handle) == self.session

    def send(self, data):
        try:
            status, written = self.native.file.WriteFile(self.handle, data)
            return status == 0 and written == len(data)
        except self.native.error:
            return False

    def receive(self):
        try:
            status, data = self.native.file.ReadFile(self.handle, MAX_MESSAGE)
            return data if status == 0 and data else None
        except self.native.error:
            return None

    def alive(self):
        try:
            self.native.pipe.PeekNamedPipe(self.handle, 1)
            return True
        except self.native.error:
            return False

    def close(self):
        self.handle.Close()


class _WindowsPipes:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        import pywintypes
        import win32api
        import win32file
        import win32pipe
        import win32security
        import win32ts

        self.api, self.file, self.pipe, self.sec, self.ts = win32api, win32file, win32pipe, win32security, win32ts
        self.error, self.attributes = pywintypes.error, pywintypes.SECURITY_ATTRIBUTES
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_session = kernel.ProcessIdToSessionId
        get_session.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
        get_session.restype = wintypes.BOOL
        session = wintypes.DWORD()
        if not get_session(os.getpid(), ctypes.byref(session)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.session = session.value
        self._client_session = kernel.GetNamedPipeClientSessionId
        self._client_session.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
        self._client_session.restype = wintypes.BOOL

    def client_session(self, handle):
        import ctypes
        from ctypes import wintypes

        session = wintypes.ULONG()
        if self._client_session(int(handle), ctypes.byref(session)):
            return session.value
        return None

    def _logon_sid(self, token):
        for sid, flags in self.sec.GetTokenInformation(token, self.sec.TokenGroups):
            if flags & 0xC0000000 == 0xC0000000:  # SE_GROUP_LOGON_ID
                return self.sec.ConvertSidToStringSid(sid)
        return None

    def offer(self, deadline):
        token = self.sec.OpenProcessToken(self.api.GetCurrentProcess(), 0x0008)  # TOKEN_QUERY
        try:
            owner = self.sec.ConvertSidToStringSid(self.sec.GetTokenInformation(token, self.sec.TokenUser)[0])
            # LocalSystem can obtain the per-logon SID of other console/RDP sessions. An elevated
            # manual service invocation has the logon SID of its own interactive session.
            if owner == _SYSTEM:
                sessions = [s["SessionId"] for s in self.ts.WTSEnumerateSessions()
                            if s["SessionId"] and s["State"] in (0, 1, 4)]
            else:
                sessions = [self.session] if self.session else []
            for session in sessions:
                if time.monotonic() >= deadline:
                    break
                try:
                    if owner == _SYSTEM:
                        peer = self.ts.WTSQueryUserToken(session)
                        try:
                            sid = self._logon_sid(peer)
                        finally:
                            peer.Close()
                    else:
                        sid = self._logon_sid(token)
                    if not sid:
                        continue
                    sa = self.attributes()
                    sa.SECURITY_DESCRIPTOR = self.sec.ConvertStringSecurityDescriptorToSecurityDescriptor(
                        f"O:{'SY' if owner == _SYSTEM else 'BA'}D:P(A;;GA;;;SY)(A;;GA;;;BA)"
                        f"(A;;0x{_CLIENT_ACCESS:X};;;{sid})", 1)
                    handle = self.pipe.CreateNamedPipe(
                        _NAME + str(session), 0x00080003,  # FILE_FLAG_FIRST_PIPE_INSTANCE | PIPE_ACCESS_DUPLEX
                        0x0000000F,  # REJECT_REMOTE_CLIENTS | TYPE_MESSAGE | READMODE_MESSAGE | NOWAIT
                        1, MAX_MESSAGE, MAX_MESSAGE, 0, sa)
                except self.error:
                    continue
                yield _Pipe(self, handle, session)
        finally:
            token.Close()

    def connect(self):
        """The desktop opens its own session's endpoint and verifies the kernel-reported owner."""
        try:
            # Identification-only SQOS prevents an untrusted pipe owner from impersonating this client.
            handle = self.file.CreateFile(_NAME + str(self.session), _CLIENT_ACCESS, 0, None, 3, 0x00110000, None)
        except self.error:
            return None
        pipe = _Pipe(self, handle)
        try:
            sd = self.sec.GetSecurityInfo(handle, self.sec.SE_KERNEL_OBJECT, self.sec.OWNER_SECURITY_INFORMATION)
            owner = self.sec.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
            if owner in (_SYSTEM, _ADMINS):
                self.pipe.SetNamedPipeHandleState(handle, 0x00000003, None, None)  # message, nonblocking
                return pipe
        except self.error:
            pass
        pipe.close()
        return None


def resident_transport():
    return _WindowsPipes() if sys.platform == "win32" else None
