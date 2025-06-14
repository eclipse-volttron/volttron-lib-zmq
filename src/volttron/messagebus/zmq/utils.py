from __future__ import annotations
import zmq.green as zmq
import hashlib
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
    
def get_vip_inproc_address(volttron_home: Path = None, instance_name: str = None) -> str:
    """
    Generate a unique inproc address for VIP communication
    
    :param volttron_home: VOLTTRON_HOME directory, used to ensure uniqueness
    :param instance_name: Instance name, used to ensure uniqueness
    :return: Unique inproc address
    """
    if volttron_home is None:
        volttron_home = os.environ.get('VOLTTRON_HOME', '/tmp/volttron_home')
        if not isinstance(volttron_home, Path):
            volttron_home = Path(volttron_home)
    
    if instance_name is None:
        # Try to get from environment, default to a hash of volttron_home
        instance_name = os.environ.get('VOLTTRON_INSTANCE', '')
        if not instance_name:
            instance_name = hashlib.md5(str(volttron_home).encode()).hexdigest()[:8]
    
    # Create a unique identifier based on volttron_home and instance_name
    unique_id = hashlib.md5(f"{volttron_home}:{instance_name}".encode()).hexdigest()[:8]
    
    # Build the address
    address = f"inproc://vip-{unique_id}"
    
    # Ensure the run directory exists
    run_dir = volttron_home / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    
    # Save to file for other processes
    address_file = run_dir / "vip_inproc_address"
    address_file.write_text(address)
    
    return address