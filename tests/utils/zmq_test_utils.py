"""
ZMQ testing utilities
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Optional, TYPE_CHECKING
import zmq.green as zmq

if TYPE_CHECKING:
    # Only imported for type checking, not at runtime
    from zmq import Socket
    
from volttron.messagebus.zmq.utils import (
    zmq_context_manager,
    zmq_socket_manager,
    safe_close_socket,
    safe_term_context,
    get_available_port
)

def create_unique_identity(prefix: str = "test") -> str:
    """Create unique identity for testing"""
    return f"{prefix}.{uuid.uuid4().hex[:8]}"

def create_test_address() -> str:
    """Create test TCP address with available port"""
    port = get_available_port()
    return f"tcp://127.0.0.1:{port}"

@contextmanager
def zmq_connection_manager(
    address: str,
    socket_type: int = zmq.DEALER,
    identity: Optional[str] = None,
    linger: int = 0
):
    """Complete connection manager for testing"""
    with zmq_context_manager() as context:
        with zmq_socket_manager(context, socket_type, linger, identity) as socket:
            if address:
                socket.connect(address)
            yield socket

class ZmqTestHelper:
    """Helper class for ZMQ testing with automatic cleanup tracking"""
    
    def __init__(self):
        self._contexts = []
        self._sockets = []
    
    def create_context(self) -> 'zmq.Context':
        """Create a ZMQ context tracked for cleanup"""
        context = zmq.Context()
        self._contexts.append(context)
        return context
    
    def create_socket(self, context: 'zmq.Context', socket_type: int, 
                     identity: Optional[str] = None, linger: int = 0) -> 'zmq.Socket':
        """Create a ZMQ socket tracked for cleanup"""
        socket = context.socket(socket_type)
        socket.setsockopt(zmq.LINGER, linger)
        
        if identity:
            socket.identity = identity.encode('utf-8')
        
        self._sockets.append(socket)
        return socket
    
    def cleanup(self):
        """Clean up all tracked contexts and sockets"""
        for socket in self._sockets:
            safe_close_socket(socket)
        self._sockets.clear()
        
        for context in self._contexts:
            safe_term_context(context)
        self._contexts.clear()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()