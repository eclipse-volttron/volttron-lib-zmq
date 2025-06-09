import logging
import time
from typing import Dict, Any, Optional, Callable, List

from volttron.types.federation import FederationBridge

_log = logging.getLogger(__name__)

class ZmqFederationBridge(FederationBridge):
    """ZeroMQ implementation of platform federation"""
    
    def __init__(self, message_bus, auth_service):
        """
        Initialize the ZMQ federation bridge
        
        :param message_bus: ZMQ message bus instance
        :param auth_service: Authentication service
        """
        self._message_bus = message_bus  # Reference to the message bus
        self._auth_service = auth_service
        self._federation_connections = {}  # platform_id -> connection_info
        self._message_handlers = []  # List of registered handlers
        
    def connect(self, platform_id: str, platform_address: str, credentials: Any) -> bool:
        """
        Connect to a remote platform using ZMQ federation
        
        :param platform_id: ID of the remote platform
        :param platform_address: Address of the remote platform
        :param credentials: Public key of the remote platform
        :return: True if connection was successful
        """
        try:
            _log.info(f"Setting up ZMQ federation connection to {platform_id} at {platform_address}")
            
            # Use message bus to establish the connection
            # The message bus has access to ZMQ context, router, etc.
            success = self._message_bus.establish_federation_connection(
                platform_id, 
                platform_address, 
                credentials
            )
            
            if success:
                # Store connection information
                self._federation_connections[platform_id] = {
                    'address': platform_address,
                    'credentials': credentials,
                    'connected': True,
                    'created_at': time.time(),
                    'last_activity': time.time(),
                    'messages_sent': 0,
                    'messages_received': 0
                }
                
            return success
            
        except Exception as e:
            _log.error(f"Error connecting to platform {platform_id}: {e}")
            return False
    
    def disconnect(self, platform_id: str) -> bool:
        """
        Disconnect from a remote platform
        
        :param platform_id: ID of the remote platform
        :return: True if disconnection was successful
        """
        if platform_id not in self._federation_connections:
            _log.warning(f"No federation connection to {platform_id} exists")
            return False
            
        try:
            # Use message bus to terminate the connection
            success = self._message_bus.terminate_federation_connection(platform_id)
            
            if success:
                # Remove connection information
                del self._federation_connections[platform_id]
                
            return success
            
        except Exception as e:
            _log.error(f"Error disconnecting from platform {platform_id}: {e}")
            return False
    
    def get_status(self, platform_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get status of federation connections
        
        :param platform_id: Optional ID to get status for a specific platform
        :return: Dictionary with status information
        """
        if platform_id:
            if platform_id not in self._federation_connections:
                return {'error': 'Connection not found'}
                
            # Return connection status
            status = dict(self._federation_connections[platform_id])
            
            # Don't return sensitive information
            if 'credentials' in status:
                status['credentials'] = '***'
                
            return status
        else:
            # Return status for all connections
            status = {}
            for pid, conn in self._federation_connections.items():
                conn_copy = dict(conn)
                if 'credentials' in conn_copy:
                    conn_copy['credentials'] = '***'
                status[pid] = conn_copy
            return status
    
    def ping(self, platform_id: str, timeout: int = 5) -> bool:
        """
        Ping a remote platform
        
        :param platform_id: ID of the remote platform
        :param timeout: Timeout in seconds
        :return: True if ping was successful
        """
        if platform_id not in self._federation_connections:
            _log.warning(f"No federation connection to {platform_id} exists")
            return False
            
        try:
            # Use message bus to ping the remote platform
            success = self._message_bus.ping_federation_connection(platform_id, timeout)
            
            if success:
                # Update last activity time
                self._federation_connections[platform_id]['last_activity'] = time.time()
                
            return success
            
        except Exception as e:
            _log.error(f"Error pinging platform {platform_id}: {e}")
            return False
    
    def send_message(self, platform_id: str, topic: str, message: Any) -> bool:
        """
        Send a message to a specific platform
        
        :param platform_id: ID of the target platform
        :param topic: Message topic
        :param message: Message content
        :return: True if message was sent successfully
        """
        if platform_id not in self._federation_connections:
            _log.warning(f"No federation connection to {platform_id} exists")
            return False
            
        try:
            # Use message bus to send the message
            success = self._message_bus.send_federation_message(platform_id, topic, message)
            
            if success:
                # Update statistics
                self._federation_connections[platform_id]['messages_sent'] += 1
                self._federation_connections[platform_id]['last_activity'] = time.time()
                
            return success
            
        except Exception as e:
            _log.error(f"Error sending message to platform {platform_id}: {e}")
            return False
    
    def register_message_handler(self, handler: Callable[[str, str, Any], None]) -> None:
        """
        Register a handler for incoming federation messages
        
        :param handler: Callback function(platform_id, topic, message)
        """
        if handler not in self._message_handlers:
            self._message_handlers.append(handler)
            
    def handle_message(self, platform_id: str, topic: str, message: Any) -> None:
        """
        Process an incoming federation message
        
        :param platform_id: ID of the source platform
        :param topic: Message topic
        :param message: Message content
        """
        if platform_id not in self._federation_connections:
            _log.warning(f"Received message from unknown platform {platform_id}")
            return
            
        # Update statistics
        self._federation_connections[platform_id]['messages_received'] += 1
        self._federation_connections[platform_id]['last_activity'] = time.time()
        
        # Notify all registered handlers
        for handler in self._message_handlers:
            try:
                handler(platform_id, topic, message)
            except Exception as e:
                _log.error(f"Error in federation message handler: {e}")