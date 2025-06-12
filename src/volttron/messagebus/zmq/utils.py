from __future__ import annotations
import zmq.green as zmq
import logging
from contextlib import contextmanager
from typing import Generator, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from zmq import Socket, Context

_log = logging.getLogger(__name__)

def safe_close_socket(socket: Optional['Socket'], linger: int = 0) -> None:
    """
    Safely close a ZMQ socket with proper error handling
    
    :param socket: ZMQ socket to close
    :param linger: Maximum time to wait for unsent messages (milliseconds)
    """
    if socket and not socket.closed:
        try:
            socket.setsockopt(zmq.LINGER, linger)
            socket.close()
            _log.debug(f"Socket closed with linger={linger}")
        except Exception as e:
            _log.debug(f"Error closing socket: {e}")

def safe_term_context(context: Optional['Context'], timeout: float = 1.0) -> None:
    """
    Safely terminate a ZMQ context with timeout
    
    :param context: ZMQ context to terminate
    :param timeout: Maximum time to wait for termination (seconds)
    """
    if context and not context.closed:
        try:
            context.term()
            _log.debug("ZMQ context terminated")
        except Exception as e:
            _log.debug(f"Error terminating context (ignored): {e}")

@contextmanager
def zmq_context_manager() -> Generator['Context', None, None]:
    """Context manager for ZMQ context with automatic cleanup"""
    context = zmq.Context()
    try:
        yield context
    finally:
        safe_term_context(context)

@contextmanager 
def zmq_socket_manager(
    context: 'Context', 
    socket_type: int, 
    linger: int = 0,
    identity: Optional[str] = None
) -> Generator['Socket', None, None]:
    """Context manager for ZMQ socket with automatic cleanup"""
    socket = context.socket(socket_type)
    socket.setsockopt(zmq.LINGER, linger)
    
    if identity:
        socket.identity = identity.encode('utf-8')
    
    try:
        yield socket
    finally:
        safe_close_socket(socket, linger=linger)

def get_available_port() -> int:
    """Get an available TCP port for testing"""
    import socket
    with socket.socket() as sock:
        sock.bind(('', 0))
        return sock.getsockname()[1]