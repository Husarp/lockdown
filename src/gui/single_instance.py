"""Only one Lockdown GUI/tray process: a second launch asks the first one to show its window, then exits."""
import queue
import socket
import threading

PORT = 47391  # localhost only


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

    def serve():
        while True:
            conn, _ = srv.accept()
            conn.close()
            events.put("open")

    threading.Thread(target=serve, daemon=True).start()
    return True
