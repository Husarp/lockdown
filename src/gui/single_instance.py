"""Only one Lockdown GUI/tray process: a second launch asks the first one to show its window, then exits."""
import queue
import socket
import threading

PORT = 47391  # localhost only
_server: list = []   # the listening socket of this process, once acquire() won (release() closes it)


def running() -> bool:
    """Is Lockdown's GUI running? (Without asking it to show its window.)"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        probe.bind(("127.0.0.1", PORT))
        return False
    except OSError:
        return True
    finally:
        probe.close()


def acquire(events: queue.Queue) -> bool:
    """True if this is the first instance (it then puts "open" into `events` whenever another launch happens).
    False if another instance is running (it has been told to show itself)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        srv.bind(("127.0.0.1", PORT))
    except OSError:
        srv.close()
        try:
            socket.create_connection(("127.0.0.1", PORT), timeout=2).close()
        except OSError:
            pass
        return False
    srv.listen(5)
    _server.append(srv)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:     # (released: this process hands over to another one)
                return
            conn.close()
            events.put("open")

    threading.Thread(target=serve, daemon=True).start()
    return True


def release():
    """Let go of the port before this process ends, so the instance that takes over can have it (the new window falling
    back to the old one). Nothing happens when this process doesn't hold it."""
    while _server:
        srv = _server.pop()
        try:
            srv.shutdown(socket.SHUT_RDWR)    # (wakes the accept() waiting in serve(); Windows refuses it on a listener)
        except OSError:
            pass
        try:
            srv.close()
        except OSError:
            pass
