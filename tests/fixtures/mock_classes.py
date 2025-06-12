import uuid
import time
import zmq
from typing import Any, List, Dict, Optional, Union, Protocol, runtime_checkable
from unittest.mock import Mock

@runtime_checkable
class SocketLike(Protocol):
    """Protocol defining the socket interface we need for testing"""
    identity: bytes
    router_mandatory: bool
    sndtimeo: int
    tcp_keepalive: bool
    tcp_keepalive_idle: int
    tcp_keepalive_intvl: int
    tcp_keepalive_cnt: int
    zap_domain: bytes
    
    def bind(self, address: str) -> None: ...
    def connect(self, address: str) -> None: ...
    def send_multipart(self, frames: List[Any], flags: int = 0, copy: bool = True) -> None: ...
    def recv_multipart(self, flags: int = 0, copy: bool = True) -> List[Any]: ...
    def close(self, linger: int = 1000) -> None: ...
    def getsockopt(self, option: int) -> Any: ...
    def set_hwm(self, value: int) -> None: ...
    def get_monitor_socket(self) -> 'SocketLike': ...
    def poll(self, timeout: Optional[int] = None) -> int: ...


class MockZmqSocket:
    """Enhanced mock ZMQ socket that tracks method calls"""
    
    def __init__(self, socket_type: int = zmq.ROUTER):
        # Socket attributes
        self.identity = b"test_socket_" + str(uuid.uuid4()).encode()
        self.router_mandatory = False
        self.sndtimeo = 1000
        self.tcp_keepalive = False
        self.tcp_keepalive_idle = 180
        self.tcp_keepalive_intvl = 20
        self.tcp_keepalive_cnt = 6
        self.zap_domain = b""
        self.socket_type = socket_type
        
        # Additional ZMQ socket attributes needed for Address.bind()
        self.ipv6 = False
        self.curve_server = False
        self.curve_secretkey = b""
        self.curve_serverkey = b""
        self.curve_publickey = b""
        self.plain_server = False
        self.plain_username = b""
        self.plain_password = b""
        self.last_endpoint = b"tcp://127.0.0.1:12345"  # Mock endpoint
        
        # Message tracking for testing
        self._sent_messages: List[Dict[str, Any]] = []
        self._received_messages: List[List[bytes]] = []
        self._bound_addresses: List[str] = []
        self._connected_addresses: List[str] = []
        self._closed = False
        self._hwm = 1000
        
        # Method call tracking
        self._method_calls = {
            'bind': [],
            'connect': [],
            'close': [],
            'send_multipart': [],
            'recv_multipart': [],
            'set_hwm': [],
            'getsockopt': []
        }
        
    def bind(self, address: str) -> None:
        """Bind to address"""
        self._method_calls['bind'].append(address)
        if self._closed:
            raise zmq.ZMQError("Socket is closed")
        self._bound_addresses.append(address)
        # Update last_endpoint to reflect the bound address
        self.last_endpoint = address.encode('utf-8')
        
    def connect(self, address: str) -> None:
        """Connect to address"""
        self._method_calls['connect'].append(address)
        if self._closed:
            raise zmq.ZMQError("Socket is closed")
        self._connected_addresses.append(address)
        
    def send_multipart(self, frames: List[Any], flags: int = 0, copy: bool = True) -> None:
        """Send multipart message"""
        self._method_calls['send_multipart'].append({
            'frames': frames, 'flags': flags, 'copy': copy
        })
        
        if self._closed:
            raise zmq.ZMQError("Socket is closed")
        if flags & zmq.NOBLOCK and len(self._sent_messages) > 100:
            raise zmq.ZMQError(errno=zmq.EAGAIN)
            
        # Convert frames to bytes for consistent storage  
        byte_frames = []
        for frame in frames:
            if hasattr(frame, 'bytes'):  # zmq.Frame
                byte_frames.append(frame.bytes)
            elif isinstance(frame, bytes):
                byte_frames.append(frame)
            else:
                byte_frames.append(str(frame).encode('utf-8'))
                
        self._sent_messages.append({
            'frames': byte_frames,
            'flags': flags,
            'copy': copy,
            'timestamp': time.time()
        })
        
    def recv_multipart(self, flags: int = 0, copy: bool = True) -> List[bytes]:
        """Receive multipart message"""
        self._method_calls['recv_multipart'].append({
            'flags': flags, 'copy': copy
        })
        
        if self._closed:
            raise zmq.ZMQError("Socket is closed")
        if self._received_messages:
            return self._received_messages.pop(0)
        if flags & zmq.NOBLOCK:
            raise zmq.ZMQError(errno=zmq.EAGAIN)
        return []
        
    def close(self, linger: int = 1000) -> None:
        """Close socket"""
        self._method_calls['close'].append(linger)
        self._closed = True
        
    def getsockopt(self, option: int) -> Union[int, bytes]:
        """Get socket option"""
        self._method_calls['getsockopt'].append(option)
        option_map = {
            zmq.SNDBUF: 1024,
            zmq.RCVBUF: 1024,
            zmq.HWM: self._hwm,
            zmq.IDENTITY: self.identity,
        }
        return option_map.get(option, 0)
        
    def setsockopt(self, option: int, value: Any) -> None:
        """Set socket option"""
        if option == zmq.HWM:
            self._hwm = int(value)
        elif option == zmq.IDENTITY:
            self.identity = bytes(value) if not isinstance(value, bytes) else value
        
    def set_hwm(self, value: int) -> None:
        """Set high water mark"""
        self._method_calls['set_hwm'].append(value)
        self._hwm = value
        
    def get_monitor_socket(self) -> 'MockZmqSocket':
        """Get monitor socket"""
        return MockZmqSocket(zmq.PAIR)
        
    def poll(self, timeout: Optional[int] = None) -> int:
        """Poll for events"""
        return zmq.POLLIN if self._received_messages else 0
        
    # Test helper methods (unchanged)
    def add_received_message(self, frames: List[bytes]) -> None:
        """Add message to receive queue"""
        self._received_messages.append(frames)
        
    def get_sent_messages(self) -> List[Dict[str, Any]]:
        """Get sent messages"""
        return self._sent_messages.copy()
        
    def clear_sent_messages(self) -> None:
        """Clear sent messages"""
        self._sent_messages.clear()
        
    def get_last_sent_message(self) -> Optional[Dict[str, Any]]:
        """Get last sent message"""
        return self._sent_messages[-1] if self._sent_messages else None
        
    def was_bound_to(self, address: str) -> bool:
        """Check if bound to address"""
        return address in self._bound_addresses
        
    def was_connected_to(self, address: str) -> bool:
        """Check if connected to address"""
        return address in self._connected_addresses
        
    def was_method_called(self, method_name: str) -> bool:
        """Check if method was called"""
        return len(self._method_calls.get(method_name, [])) > 0
        
    def get_method_calls(self, method_name: str) -> List[Any]:
        """Get method call history"""
        return self._method_calls.get(method_name, [])


class MockZmqPoller:
    """Simple mock ZMQ poller"""
    
    def __init__(self):
        self._sockets: Dict[Any, int] = {}
        
    def register(self, socket: Any, flags: int = zmq.POLLIN) -> None:
        """Register socket"""
        self._sockets[socket] = flags
        
    def unregister(self, socket: Any) -> None:
        """Unregister socket"""
        self._sockets.pop(socket, None)
        
    def poll(self, timeout: Optional[int] = None) -> List[tuple]:
        """Poll sockets"""
        result = []
        for socket, flags in self._sockets.items():
            events = 0
            if hasattr(socket, '_received_messages') and socket._received_messages:
                events |= zmq.POLLIN
            if events & flags:
                result.append((socket, events))
        return result


class MockZmqContext:
    """Simple mock ZMQ context"""
    
    def __init__(self):
        self._sockets: List[MockZmqSocket] = []
        self._closed = False
        
    def socket(self, socket_type: int) -> MockZmqSocket:
        """Create socket"""
        if self._closed:
            raise zmq.ZMQError("Context is closed")
        sock = MockZmqSocket(socket_type)
        self._sockets.append(sock)
        return sock
        
    def term(self) -> None:
        """Terminate context"""
        self._closed = True
        for sock in self._sockets:
            if not sock._closed:
                sock.close()
        self._sockets.clear()
        
    def set(self, option: int, value: int) -> None:
        """Set context option"""
        pass  # No-op for testing
        
    @staticmethod
    def instance() -> 'MockZmqContext':
        """Get instance (for compatibility)"""
        return MockZmqContext()

class MockAgent:
    """Mock VOLTTRON agent for testing"""
    
    def __init__(self, identity, context=None):
        self.identity = identity.encode() if isinstance(identity, str) else identity
        self.context = context or zmq.Context()
        self.socket = None
        self.connected = False
        self.received_messages = []
        
    def connect_to_router(self, router_address):
        self.socket = self.context.socket(zmq.DEALER)
        self.socket.identity = self.identity
        self.socket.connect(router_address)
        self.connected = True
        
    def send_vip_message(self, recipient, subsystem, *args):
        if not self.connected:
            raise RuntimeError("Not connected to router")
            
        frames = [recipient, b"VIP1", b"", b"test_msg_id", subsystem]
        frames.extend(args)
        self.socket.send_multipart(frames)
        
    def receive_message(self, timeout=1000):
        if not self.connected:
            raise RuntimeError("Not connected to router")
            
        if self.socket.poll(timeout):
            frames = self.socket.recv_multipart()
            self.received_messages.append(frames)
            return frames
        return None
        
    def disconnect(self):
        if self.socket:
            self.socket.close()
        self.connected = False