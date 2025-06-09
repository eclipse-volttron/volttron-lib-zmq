import logging
import time
from typing import Dict, List, Any, Optional, Callable

from volttron.types.federation import FederationBridge
from volttron.messagebus.zmq.router import Router

_log = logging.getLogger(__name__)

class ZmqFederationBridge(FederationBridge):
    """ZeroMQ implementation of platform federation using the Router"""
    
    def __init__(self, router: Router, auth_service):
        """
        Initialize the ZMQ federation bridge
        
        :param router: ZMQ router instance
        :param auth_service: Authentication service
        """
        self._router = router
        self._auth_service = auth_service
        self._connected_platforms = {}
    
    def connect(self, platform_id: str, platform_address: str, credentials: Any) -> bool:
        """
        Connect to a remote platform
        
        :param platform_id: ID of the remote platform
        :param platform_address: Address of the remote platform
        :param credentials: Public key credential for the remote platform
        :return: True if connection was successful
        """
        try:
            # First, register the platform with the auth service
            self._auth_service.add_federation_platform(platform_id, credentials)
            
            # Create instance config for the router's routing service
            instance_config = {
                "instance-name": platform_id,
                "serverkey": credentials,  # This is the public key
                "vip-address": platform_address
            }
            
            # Use the router's routing service to connect to the platform
            routing_service = self._router.routing_service
            
            # Build connection using normalmode_platform_connection
            # We'll use the existing handle_subsystem method, but we need to create
            # frames that match what the method expects
            frames = [
                b"", b"", b"VIP1", b"", b"", b"routing_table", b"normalmode_platform_connection", 
                instance_config
            ]
            routing_service.handle_subsystem(frames)
            
            # Store connection info
            self._connected_platforms[platform_id] = {
                "address": platform_address,
                "credentials": credentials,
                "connected_at": time.time(),
                "last_heartbeat": time.time()
            }
            
            _log.info(f"Connected to platform: {platform_id} at {platform_address}")
            return True
            
        except Exception as e:
            _log.error(f"Error connecting to platform {platform_id}: {e}")
            
            # Clean up auth service registration if connection failed
            try:
                self._auth_service.remove_federation_platform(platform_id)
            except:
                pass
                
            return False
    
    def disconnect(self, platform_id: str) -> bool:
        """
        Disconnect from a remote platform
        
        :param platform_id: ID of the remote platform
        :return: True if disconnection was successful
        """
        try:
            # Get the routing service
            routing_service = self._router.routing_service
            
            # Disconnect the platform
            routing_service.disconnect_external_instances(platform_id)
            
            # Remove from auth service
            self._auth_service.remove_federation_platform(platform_id)
            
            # Remove from connected platforms
            if platform_id in self._connected_platforms:
                del self._connected_platforms[platform_id]
                
            _log.info(f"Disconnected from platform: {platform_id}")
            return True
            
        except Exception as e:
            _log.error(f"Error disconnecting from platform {platform_id}: {e}")
            return False
    
    def get_status(self, platform_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get status of federation connections
        
        :param platform_id: Optional ID to get status for a specific platform
        :return: Dictionary with status information
        """
        # Get the routing service
        routing_service = self._router.routing_service
        
        # Get connected platforms from routing service
        connected_platforms = routing_service.get_connected_platforms()
        
        if platform_id:
            # Return status for specific platform
            if platform_id in self._connected_platforms:
                status = dict(self._connected_platforms[platform_id])
                status['is_connected'] = platform_id in connected_platforms
                
                # Don't return sensitive information
                if 'credentials' in status:
                    status['credentials'] = "***"
                    
                return status
            else:
                return {"error": f"Platform {platform_id} not found"}
        else:
            # Return status for all platforms
            result = {}
            for platform_id, info in self._connected_platforms.items():
                platform_status = dict(info)
                platform_status['is_connected'] = platform_id in connected_platforms
                
                # Don't return sensitive information
                if 'credentials' in platform_status:
                    platform_status['credentials'] = "***"
                    
                result[platform_id] = platform_status
                
            return result
    
    def ping(self, platform_id: str, timeout: int = 5) -> bool:
        """
        Ping a remote platform to check connection health
        
        :param platform_id: ID of the remote platform
        :param timeout: Timeout in seconds
        :return: True if ping was successful
        """
        # Get the routing service
        routing_service = self._router.routing_service
        
        # Get connected platforms from routing service
        connected_platforms = routing_service.get_connected_platforms()
        
        # Check if platform is connected
        is_connected = platform_id in connected_platforms
        
        if is_connected and platform_id in self._connected_platforms:
            # Update last heartbeat time
            self._connected_platforms[platform_id]['last_heartbeat'] = time.time()
            return True
        else:
            return False