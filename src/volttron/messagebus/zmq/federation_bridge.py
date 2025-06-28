import logging
import time
from typing import Dict, List, Any, Optional, Callable

from volttron.types.federation import FederationBridge
#from volttron.messagebus.zmq.router import Router

_log = logging.getLogger(__name__)

class ZmqFederationBridge(FederationBridge):
    """
    ZMQ implementation of the Federation Bridge interface.
    Handles connections between distributed VOLTTRON instances.
    """
    
    def __init__(self, zmq_message_bus):
        """
        Initialize the ZMQ Federation Bridge
        
        :param zmq_message_bus: Reference to the ZmqMessageBus instance
        """
        self._message_bus = zmq_message_bus
        self._connected_platforms = {}  # Store platform connection info

    def connect(self, platform_id: str, platform_address: str, credentials: Any) -> bool:
        """
        Connect to a remote platform
        
        :param platform_id: ID of the remote platform
        :param platform_address: Address of the remote platform
        :param credentials: Public key credential for the remote platform
        :return: True if connection was successful
        """
        try:
            _log.info(f"Connecting to federation platform {platform_id} at {platform_address}")
            
            # Register with auth service
            if not self._message_bus._auth_service.add_federation_platform(platform_id, credentials):
                _log.error(f"Auth service rejected federation platform {platform_id}")
                return False
                
            _log.debug(f"Auth service accepted federation platform {platform_id}")
            
            # Store connection info
            self._connected_platforms[platform_id] = {
                "address": platform_address,
                "credentials": credentials,
                "connected": False,  # Will be set to True if connection succeeds
                "last_heartbeat": time.time()
            }
            
            try:
                # Execute router operation safely in the router thread
                connection_result = self._message_bus.execute_in_router_thread(
                    lambda: self._connect_to_platform(platform_id, platform_address)
                )
                
                if connection_result:
                    # Reset subscription cache for clean slate
                    self._message_bus.execute_in_router_thread(
                        lambda: self._reset_platform_subscriptions(platform_id)
                    )
                    _log.debug(f"Reset subscription cache for platform {platform_id}")
                    self._connected_platforms[platform_id]["connected"] = True
                    # Sync subscriptions after connection
                    self._sync_subscriptions_with_platform(platform_id)

                    return True
                else:
                    _log.error(f"Router rejected connection to platform {platform_id}")
                    return False
                    
            except Exception as e:
                _log.error(f"Error in router thread while connecting to platform {platform_id}: {e}", 
                        exc_info=True)
                return False
                
        except Exception as e:
            _log.error(f"Error connecting to federated platform {platform_id}: {e}", 
                    exc_info=True)
            return False
        
    def _connect_to_platform(self, platform_id: str, platform_address: str) -> bool:
        """Helper method to connect to a platform via the router"""
        # This method runs in the router thread
        try:
            _log.debug(f"Executing router connect operation for platform {platform_id}")
            
            # Check if router is available
            if not hasattr(self._message_bus, "_router_instance") or self._message_bus._router_instance is None:
                _log.error("Router instance not available")
                return False
                
            # Get the router instance
            router = self._message_bus._router_instance
            
            # Connect to remote platform
            # Note: Need to check the actual method name and parameters in your router implementation
            if hasattr(router, "connect_remote_platform"):
                result = router.connect_remote_platform(
                    platform_id=platform_id,
                    address=platform_address
                )
                _log.debug(f"Router connect_remote_platform result: {result}")
                return bool(result)
            elif hasattr(router.routing_service, "connect_remote_platform"):
                result = router.routing_service.connect_remote_platform(
                    platform_id=platform_id,
                    address=platform_address
                )
                _log.debug(f"Router routing_service.connect_remote_platform result: {result}")
                return bool(result)
            else:
                _log.error("Router doesn't have connect_remote_platform method")
                return False
                
        except Exception as e:
            _log.error(f"Error connecting to platform {platform_id} in router thread: {e}", 
                    exc_info=True)
            return False
    
    def disconnect(self, platform_id: str) -> bool:
        """
        Disconnect from a remote platform
        
        :param platform_id: ID of the remote platform
        :return: True if disconnection was successful
        """
        try:
            if platform_id in self._connected_platforms:
                # Execute in the router's thread for thread safety
                self._message_bus.execute_in_router_thread(
                    lambda: self._message_bus.router.routing_service.disconnect_remote_platform(platform_id)
                )
                
                # Update connection info
                self._connected_platforms[platform_id]["connected"] = False
                _log.info(f"Disconnected from federated platform: {platform_id}")
                return True
                
            _log.warning(f"Cannot disconnect: Platform {platform_id} not found")
            return False
            
        except Exception as e:
            _log.error(f"Error disconnecting from platform {platform_id}: {e}")
            return False
    
    def get_status(self, platform_id: Optional[str] = None) -> Dict[str, Dict]:
        """
        Get status of connected platforms
        
        :param platform_id: Optional ID of a specific platform to get status for
        :return: Dict containing status information for requested platform(s)
        """
        if platform_id is not None:
            # Return status for specific platform if it exists
            if platform_id in self._connected_platforms:
                return {platform_id: self._connected_platforms[platform_id]}
            return {}  # Platform not found
        
        # Return status for all platforms
        return self._connected_platforms
    
    def ping(self, platform_id: str, timeout: int = 5) -> bool:
        """
        Ping a remote platform to check connection health
        
        :param platform_id: ID of the remote platform
        :param timeout: Timeout in seconds
        :return: True if ping was successful
        """
        try:
            # Execute in the router's thread for thread safety
            is_connected = self._message_bus.execute_in_router_thread(
                lambda: self._check_platform_connection(platform_id)
            )
            
            if is_connected and platform_id in self._connected_platforms:
                # Update last heartbeat time
                self._connected_platforms[platform_id]['last_heartbeat'] = time.time()
                return True
            else:
                return False
                
        except Exception as e:
            _log.error(f"Error pinging platform {platform_id}: {e}")
            return False
            
    def _check_platform_connection(self, platform_id: str) -> bool:
        """
        Helper method to check if a platform is connected
        
        :param platform_id: ID of the remote platform
        :return: True if platform is connected
        """
        # Get the routing service
        routing_service = self._message_bus.router.routing_service
        
        # Get connected platforms from routing service
        connected_platforms = routing_service.get_connected_platforms()
        
        # Check if platform is connected
        return platform_id in connected_platforms
    
    def _reset_platform_subscriptions(self, platform_id: str):
        """Reset the subscription cache for a platform"""

        try:
            # Get router instance
            router = self._message_bus._router_instance
            if hasattr(router, 'pubsub'):
                pubsub = router.pubsub
                if hasattr(pubsub, '_ext_subscriptions') and platform_id in pubsub._ext_subscriptions:
                    pubsub._ext_subscriptions[platform_id] = {}
                    _log.debug(f"Reset _ext_subscriptions for {platform_id}")
        except Exception as e:
            _log.error(f"Error resetting subscriptions: {e}")
            
    def _sync_subscriptions_with_platform(self, platform_id: str):
        """Sync local subscriptions to remote platform"""
        
        _log.debug(f"Syncing subscriptions with platform {platform_id}")
        
        # This needs to run in the router thread to safely access pubsub
        self._message_bus.execute_in_router_thread(
            lambda: self._do_sync_subscriptions(platform_id)
        )
        
    def _do_sync_subscriptions(self, platform_id: str):
        """Actual subscription sync (runs in router thread)"""

        try:
            # Get router instance
            router = self._message_bus._router_instance
            if not hasattr(router, 'pubsub'):
                _log.error("Router has no pubsub attribute")
                return
                
            pubsub = router.pubsub
            
            # Get local subscriptions
            local_subscriptions = {}
            try:
                # Structure depends on actual implementation
                if hasattr(pubsub, '_my_subscriptions'):
                    for bus, topics in pubsub._my_subscriptions.items():
                        if bus not in local_subscriptions:
                            local_subscriptions[bus] = []
                        for topic in topics:
                            local_subscriptions[bus].append(topic)
            except Exception as e:
                _log.error(f"Error getting local subscriptions: {e}")
                
            _log.debug(f"Sending subscriptions to {platform_id}: {local_subscriptions}")
            
            # Send subscription data to remote platform
            # This assumes socket is available in router thread
            frames = [
                platform_id,
                "",
                "VIP1",
                "",
                "",
                "pubsub",
                "register_external_subscriptions",
                bytes(repr(local_subscriptions), 'utf-8')
            ]
            
            try:
                router.socket.send_multipart(frames, flags=0)
                _log.debug(f"Sent subscription sync frames to {platform_id}")
            except Exception as e:
                _log.error(f"Error sending subscription sync frames: {e}")
                
        except Exception as e:
            _log.error(f"Error in _do_sync_subscriptions: {e}")


    
