"""Relay Bridge Emulator and Telemetry Stream Generator.

Faithfully reproduces:
1. `tracker-smoothtrack/android/relay.c`:
   UDP listener (127.0.0.1:udp_port) -> TCP reverse client (127.0.0.1:tcp_port) with TCP_NODELAY.
2. Mobile SmoothTrack App:
   Streams 48-byte IEEE 754 little-endian pose frames at 60Hz over UDP.
3. Host OpenTrack Receiver:
   TCP server on 127.0.0.1 accepting relay connection and draining frames.
"""

import errno
import math
import select
import socket
import threading
import time
from typing import List, Optional, Tuple

try:
    from .protocol_oracle import (
        FRAME_SIZE,
        SimulatedStreamParser,
        is_pose_valid,
        pack_pose,
    )
except ImportError:
    from protocol_oracle import (
        FRAME_SIZE,
        SimulatedStreamParser,
        is_pose_valid,
        pack_pose,
    )


class RelayBridgeEmulator:
    """Python reference implementation of the C st-relay daemon (tracker-smoothtrack/android/relay.c)."""

    def __init__(self, udp_port: int = 4242, tcp_port: int = 4242, bind_ip: str = "127.0.0.1", connect_ip: str = "127.0.0.1"):
        self.udp_port = udp_port
        self.tcp_port = tcp_port
        self.bind_ip = bind_ip
        self.connect_ip = connect_ip

        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.udp_socket: Optional[socket.socket] = None
        self.tcp_socket: Optional[socket.socket] = None

        self.packets_relayed = 0
        self.bytes_relayed = 0
        self.connected_event = threading.Event()
        self.stopped_event = threading.Event()

    def start(self, retry_limit: int = 50, retry_delay: float = 0.05) -> None:
        """Starts the relay bridge in a background daemon thread."""
        self.running = True
        self.connected_event.clear()
        self.stopped_event.clear()
        self.thread = threading.Thread(target=self._run, args=(retry_limit, retry_delay), daemon=True)
        self.thread.start()

    def _run(self, retry_limit: int, retry_delay: float) -> None:
        try:
            # 1. Setup UDP listener (SmoothTrack sends to 127.0.0.1:udp_port)
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp_socket.bind((self.bind_ip, self.udp_port))
            self.udp_socket.settimeout(0.2)

            # 2. Setup TCP connection to reverse port
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            connected = False
            for _ in range(retry_limit):
                if not self.running:
                    break
                try:
                    self.tcp_socket.connect((self.connect_ip, self.tcp_port))
                    connected = True
                    break
                except (socket.error, ConnectionRefusedError):
                    time.sleep(retry_delay)

            if not connected or not self.running:
                return

            self.connected_event.set()

            # 3. Datagram forwarding loop
            while self.running:
                # Check if TCP peer disconnected (FIN sent)
                try:
                    r, _, _ = select.select([self.tcp_socket], [], [], 0)
                    if r:
                        peek = self.tcp_socket.recv(1, socket.MSG_PEEK)
                        if not peek:
                            # Peer closed connection
                            break
                except (socket.error, ConnectionResetError, BrokenPipeError):
                    break

                try:
                    data, _ = self.udp_socket.recvfrom(256)
                    if not data:
                        break
                    # Send over TCP
                    self.tcp_socket.sendall(data)
                    self.packets_relayed += 1
                    self.bytes_relayed += len(data)
                except socket.timeout:
                    continue
                except (socket.error, BrokenPipeError, ConnectionResetError):
                    # Host closed connection or disconnected
                    break

        finally:
            self.running = False
            if self.udp_socket:
                try:
                    self.udp_socket.close()
                except Exception:
                    pass
            if self.tcp_socket:
                try:
                    self.tcp_socket.close()
                except Exception:
                    pass
            self.stopped_event.set()

    def stop(self) -> None:
        """Stops the bridge and waits for termination."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)


class MockHostServer:
    """Emulates OpenTrack's Host QTcpServer listening for the reverse relay connection."""

    def __init__(self, port: int = 4242, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.server_socket: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.parser = SimulatedStreamParser()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.client_connected = threading.Event()
        self.client_disconnected = threading.Event()

    def start(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(5)
        self.server_socket.settimeout(0.2)

        self.running = True
        self.client_connected.clear()
        self.client_disconnected.clear()
        self.thread = threading.Thread(target=self._accept_and_read, daemon=True)
        self.thread.start()

    def _accept_and_read(self) -> None:
        try:
            while self.running:
                # 1. Accept incoming client connection
                while self.running and not self.client_socket:
                    try:
                        client, _ = self.server_socket.accept()
                        self.client_socket = client
                        self.client_socket.settimeout(0.1)
                        self.client_connected.set()
                    except socket.timeout:
                        continue
                    except Exception:
                        return

                # 2. Ingestion loop matching smoothtrack::run()
                while self.running and self.client_socket:
                    try:
                        chunk = self.client_socket.recv(1024)
                        if not chunk:
                            # Client disconnected
                            break
                        self.parser.feed_bytes(chunk)
                    except socket.timeout:
                        continue
                    except (socket.error, ConnectionResetError):
                        break

                if self.client_socket:
                    try:
                        self.client_socket.close()
                    except Exception:
                        pass
                    self.client_socket = None
                    self.client_connected.clear()
                    self.client_disconnected.set()
        finally:
            self.client_disconnected.set()
            if self.client_socket:
                try:
                    self.client_socket.close()
                except Exception:
                    pass
                self.client_socket = None

    def stop(self) -> None:
        self.running = False
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
        if self.client_socket:
            try:
                self.client_socket.close()
            except Exception:
                pass
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)


class MockSmoothTrackApp:
    """Emulates the SmoothTrack Mobile Application sending UDP telemetry packets."""

    def __init__(self, target_port: int = 4242, target_ip: str = "127.0.0.1"):
        self.target_port = target_port
        self.target_ip = target_ip
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_pose(self, x: float, y: float, z: float, yaw: float, pitch: float, roll: float) -> int:
        packet = pack_pose(x, y, z, yaw, pitch, roll)
        return self.socket.sendto(packet, (self.target_ip, self.target_port))

    def send_raw(self, raw_bytes: bytes) -> int:
        return self.socket.sendto(raw_bytes, (self.target_ip, self.target_port))

    def close(self) -> None:
        try:
            self.socket.close()
        except Exception:
            pass
