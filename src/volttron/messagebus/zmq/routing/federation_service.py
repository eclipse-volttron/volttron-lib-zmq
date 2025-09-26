import json
import logging
import time
from pathlib import Path
from typing import Dict

import gevent
import httpx
from httpx import HTTPError
from watchdog.events import FileSystemEventHandler
from watchdog_gevent import Observer

from volttron.client.known_identities import PLATFORM
from volttron.messagebus.zmq.routing import RoutingService
from volttron.server.server_options import ServerOptions
from volttron.types import MessageBus
from volttron.types.auth.auth_credentials import VolttronCredentials
from volttron.types.auth.auth_service import AuthService
from volttron.utils import format_timestamp

_log = logging.getLogger(__name__)

DEFAULT_GROUP = "default"
DEFAULT_RETRY_PERIOD = 30  # seconds
logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
logging.getLogger("httpcore.connection").setLevel(logging.WARNING)

class _PlatformInstance:
    """Represents a connected remote VOLTTRON platform"""
    
    def __init__(self, platform_id: str, address: str, public_credentials: str, group: str = DEFAULT_GROUP):
        self.platform_id = platform_id
        self.address = address
        self.public_credentials = public_credentials
        self.group = group
        self.connected = False
        self.last_heartbeat = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization"""
        return {
            "id": self.platform_id,
            "address": self.address,
            "public_credentials": self.public_credentials,
            "group": self.group,
            "connected": self.connected,
            "last_heartbeat": self.last_heartbeat
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> '_PlatformInstance':
        """Create instance from dictionary"""
        instance = cls(
            platform_id=data["id"],
            address=data["address"],
            public_credentials=data["public_credentials"],
            group=data.get("group", DEFAULT_GROUP)
        )
        instance.connected = data.get("connected", False)
        instance.last_heartbeat = data.get("last_heartbeat")
        return instance

class FederationService:
    """
    class that manages connections between multiple VOLTTRON platforms.
    Enables cross-platform message routing and RPC calls.
    """
    
    def __init__(self, options: ServerOptions, auth_service: AuthService, routing_service: RoutingService,
                 messagebus: MessageBus, **kwargs):
        
        self._options = options
        self._auth_service = auth_service
        self._routing_service = routing_service
        self._messagebus = messagebus
        self._registered_platforms: Dict[str, _PlatformInstance] = {}
        self._registry_url = None
        self._httpx_client = httpx.Client(timeout=10.0)
        self._federation_config_path = Path(self._options.volttron_home) / "federation_config.json"
        self._federation_enabled = self._options.enable_federation
        self._retry_period = DEFAULT_RETRY_PERIOD
        self._registry_connection_successful = False
        self._registry_last_attempt = 0
        
        # Load any existing federation configuration
        self._load_config()

        self._registry_url = options.federation_url.split('#')[0]
        # record of greenlets that handle replay from cache. one per external server
        self.cache_publisher_greenlets = {}  # {server_address: greenlet}
        # stop event for the cache_publisher_greenlets
        self.stop_events = {}  # {server_address: stop event}
        # Register the on_connect callback with Router
        self._routing_service.register("on_connect", self.on_connect)
        self._routing_service.register("on_temp_disconnect", self.on_disconnect)
        # Register self with platform look up service and periodically poll for changes in federated volttron instances
        self._start_federation()

        # TODO - combine with registered_platforms?
        self._federation_platforms = {}
        self._federation_connections = {}  # Dict[platform_id, connection_info]

        # Setup federation file watcher
        self._federation_observer = None
        self._setup_federation_watcher()


    def _load_config(self):
        """Load federation configuration from file"""
        if not self._federation_config_path.exists():
            _log.info("No federation configuration file found, creating new configuration")
            self._save_config()
            return
            
        try:
            config = json.loads(self._federation_config_path.read_text())
            self._registry_url = config.get('registry_url')
            
            # Load platforms from config
            platforms_data = config.get('platforms', [])
            for platform_data in platforms_data:
                try:
                    platform = _PlatformInstance.from_dict(platform_data)
                    self._registered_platforms[platform.platform_id] = platform
                except Exception as e:
                    _log.error(f"Error loading platform from config: {e}")
                    
            _log.info(f"Loaded federation configuration with {len(self._registered_platforms)} platforms")
        except Exception as e:
            _log.error(f"Error loading federation config: {e}")
            # Create a new config file if loading fails
            self._save_config()
    
    def _save_config(self):
        """Save federation configuration to file"""
        try:
            config = {
                'registry_url': self._registry_url,
                'platforms': [platform.to_dict()
                                for platform in self._registered_platforms.values()
                                  if platform.platform_id != self._options.instance_name]
            }
            
            self._federation_config_path.write_text(json.dumps(config, indent=2))
            _log.debug("Saved federation configuration")
        except Exception as e:
            _log.error(f"Error saving federation config: {e}")

    def _start_federation(self):

        if self._federation_enabled:
            # self._federation_bridge = self._messagebus.create_federation_bridge()
            # if not self._federation_bridge:
            #     _log.error("Federation bridge not available, cannot start federation service")
            #     return
            
            # If we have a registry URL, register and discover platforms
            if self._registry_url:
                # Schedule registration with retry mechanism
                self._registry_greenlet = gevent.spawn(self._registry_connection_loop)
            else:
                # If no registry, just connect to platforms from config
                self._register_stored_platforms()

    def _registry_connection_loop(self):
        """Periodically try to register and get platforms from the registry"""
        while True:
            try:
                if not self._registry_connection_successful:
                    # Handle registration only if not successful yet
                    self._registry_connection_successful = self._attempt_registration()
                else:
                    # If already registered, just update platform list
                    self._discover_platforms()
            except Exception as e:
                _log.error(f"Error in registry connection loop: {e}")
                self._registry_connection_successful = False
                    
            # Sleep before next attempt
            self._registry_last_attempt = time.time()
            gevent.sleep(self._retry_period)

    def _attempt_registration(self) -> bool:
        """Try to register with federation registry"""
        if not self._registry_url:
            return False
            
        _log.debug(f"Attempting to register with federation registry at {self._registry_url}")
        success = self.register_with_federation(self._registry_url)
        
        if success:
            _log.info("Successfully registered with federation registry")
            # Do an initial platform discovery
            self._discover_platforms()
        else:
            _log.warning(f"Failed to register with registry, will retry in {self._retry_period} seconds")
        
        return success

    def _discover_platforms(self):
        """Discover platforms and manage connections"""
        count = self.discover_and_register_platforms()
        _log.debug(f"Platform discovery found {count} platforms")
    
    
    def _register_stored_platforms(self):
        """register remote platforms stored in configuration"""
        # TODO - do we need to call this method for config that is already saved. In theory if entries exists in
        #  federation config, it should have corresponding file in credential store too. Check.
        for platform_id, platform in list(self._registered_platforms.items()):
            _log.info(f"Registering stored platform {platform_id} at {platform.address}")
            self._auth_service.register_remote_platform(platform.platform_id, platform.public_credentials)
            _log.info(f"Registered stored platform {platform_id} at {platform.address}")

    def shutdown(self, **kwargs):
        """Handle shutdown tasks"""
        _log.info(f"Stopping Federation Service")
        self._is_running = False
        self._federation_observer.stop()
        self._federation_observer.join()

        # Stop connection loop greenlet
        if hasattr(self, '_registry_greenlet'):
            self._registry_greenlet.kill()
        # TODO - should we just disconnect from federation servers or completely remove all entries and rebuild
        #  federation entries from platform lookup on next startup
        # If registered with a federation registry, attempt to unregister
        if self._registry_url and self._options.instance_name and self._registry_connection_successful:
            try:
                self._httpx_client.delete(
                    f"{self._registry_url}/platform/{self._options.instance_name}",
                    headers={"accept": "application/json"}
                )
                _log.info(f"Unregistered from federation registry at {self._registry_url}")
            except Exception as e:
                _log.error(f"Failed to unregister from federation registry: {e}")
        
        # Close httpx client
        self._httpx_client.close()
        
        # Disconnect from all platforms
        self._disconnect_all_platforms()

        # Save final state to configuration
        self._save_config()
    
    def register_with_federation(self, registry_url: str) -> bool:
        """
        Register this platform with a federation registry.
        
        :param registry_url: URL of the federation registry service
        :return: True if registration was successful
        """
        self._registry_url = registry_url
        
        # Determine platform ID
        local_platform_id = self._options.instance_name
        if not local_platform_id:
            _log.error("Cannot register with federation: no platform ID available")
            return False
            
        # Get our platform's address
        if not self._options.address or len(self._options.address) == 0:
            _log.error("Cannot register with federation: no platform address available")
            return False
            
        # Use the first address as our externally reachable address
        platform_address = self._options.address[0]
        
        # Simple validation of the address format
        if not (platform_address.startswith('tcp://') or 
                platform_address.startswith('ipc://') or 
                platform_address.startswith('inproc://')):
            _log.error(f"Invalid address format for federation: {platform_address}")
            _log.error("Address should start with tcp://, ipc://, or inproc://")
            return False
        
        # Get our platform's public credentials using auth service
        public_credentials = None
        try:
            # Use auth_service directly instead of VIP
            public_credentials = self._auth_service.get_credentials(identity=PLATFORM).get_public_part()
            if not public_credentials:
                _log.error("Auth service returned empty platform public key")
        except Exception as e:
            _log.error(f"Error getting platform public key from auth service: {e}")
            
        if not public_credentials:
            _log.error("Cannot register with federation: no public credential available")
            return False
            
        # Register our platform with the federation registry
        _log.info(f"Registering platform {local_platform_id} with federation registry at {registry_url}")
        registration_data = {
            "address": platform_address,
            "group": DEFAULT_GROUP,  # Default group can be customized
            "id": local_platform_id,
            "public_credentials": public_credentials  # Match API field name
        }
        
        try:
            # Post our platform to the registry
            response = self._httpx_client.post(
                f"{registry_url}/platform", 
                headers={"accept": "application/json", "Content-Type": "application/json"},
                json=registration_data,
                timeout=5.0  # Short timeout for responsiveness
            )
            response.raise_for_status()
            _log.info(f"Successfully registered with federation registry: {response.text}")
            
            # Save the configuration with registry URL
            self._save_config()
            
            # Discover and connect to other platforms
            return self.discover_and_register_platforms() >= 0
            
        except HTTPError as e:
            status_code = e.response.status_code if hasattr(e, 'response') else "unknown"
            _log.error(f"HTTP error registering with federation registry (status {status_code}): {e}")
            return False
        except Exception as e:
            _log.error(f"Failed to register with federation registry: {e}")
            return False
    
    def discover_and_register_platforms(self) -> int:
        """
        Discover platforms from platform lookup service, register remote platform credentials with
        local auth service(which  persists the remote server credentials in credential store), and save the remote
        platform details in federation config
        
        :return: Number of platforms registered, -1 if error
        """
        if not self._registry_url:
            _log.error("Cannot discover platforms: no registry URL configured")
            return -1
            
        try:
            # Get list of platforms from registry
            response = self._httpx_client.get(
                f"{self._registry_url}/platforms", 
                headers={"accept": "application/json"},
                timeout=5.0  # Short timeout for responsiveness
            )
            response.raise_for_status()
            platforms_data = response.json()
            
            connected_count = 0
            local_platform_id = self._options.instance_name
            
            # Process each platform
            for platform_data in platforms_data:
                # Skip ourselves
                if platform_data["id"] == local_platform_id:
                    continue
                    
                platform_id = platform_data["id"]
                
                try:
                    # Check if we already have this platform
                    if platform_id in self._registered_platforms:
                        existing_platform = self._registered_platforms[platform_id]
                        
                        # Check if anything has changed that requires reconnection
                        if (existing_platform.address != platform_data["address"] or 
                            existing_platform.public_credentials != platform_data["public_credentials"]):
                            
                            _log.info(f"Platform {platform_id} details changed, reconnecting")
                            # Disconnect existing connection
                            try:
                                self._disconnect_platform(existing_platform)
                            except:
                                _log.warning(f"Error disconnecting from platform {platform_id} before update")
                                
                            # Create updated platform instance
                            platform = _PlatformInstance(
                                platform_id=platform_id,
                                address=platform_data["address"],
                                public_credentials=platform_data["public_credentials"],
                                group=platform_data.get("group", DEFAULT_GROUP)
                            )
                            
                            # Store updated platform
                            self._registered_platforms[platform_id] = platform
                            
                            # register updated platform details
                            self._auth_service.register_remote_platform(platform.platform_id,
                                                                        platform.public_credentials)
                            connected_count += 1
                        else:
                            # Platform exists with same details, just count it
                            connected_count += 1 if existing_platform.connected else 0
                    else:
                        # New platform discovered
                        _log.info(f"Discovered new platform: {platform_id}")
                        
                        # Create _PlatformInstance object
                        platform = _PlatformInstance(
                            platform_id=platform_id,
                            address=platform_data["address"],
                            public_credentials=platform_data["public_credentials"],
                            group=platform_data.get("group", DEFAULT_GROUP)
                        )
                        
                        # Store in our registry
                        self._registered_platforms[platform_id] = platform
                        
                        # Register
                        _log.info(f"Registering federated platform: {platform_id} at {platform.address}")
                        self._auth_service.register_remote_platform(platform.platform_id,
                                                                    platform.public_credentials)
                        connected_count += 1
                except Exception as e:
                    _log.error(f"Error processing platform {platform_id}: {e}")
                    
            # Save updated configuration
            self._save_config()
            
            return connected_count
            
        except HTTPError as e:
            status_code = e.response.status_code if hasattr(e, 'response') else "unknown"
            _log.error(f"HTTP error discovering platforms (status {status_code}): {e}")
            return -1
        except Exception as e:
            _log.error(f"Failed to discover platforms: {e}")
            return -1
    
    def _disconnect_platform(self, platform: _PlatformInstance):
        """Disconnect from a specific platform"""
        self._routing_service.disconnect_external_instances(platform.platform_id)
    
    def _disconnect_all_platforms(self):
        """Disconnect from all connected platforms"""
        for platform_id, platform in list(self._registered_platforms.items()):
            try:
                if platform.connected:
                    self._routing_service.disconnect_external_instances(platform_id)
            except Exception as e:
                _log.error(f"Error disconnecting from platform {platform_id}: {e}")

    def _setup_federation_watcher(self):
        """Setup watchdog observer for federation config file"""
        try:
            self._federation_observer = Observer()
            handler = FederationConfigHandler(self)

            # Watch the VOLTTRON_HOME directory for federation_config.json changes
            watch_dir = str(self._federation_config_path.parent)
            self._federation_observer.schedule(handler, watch_dir, recursive=False)
            self._federation_observer.start()

            _log.info(f"Federation config watcher started for: {self._federation_config_path}")

        except Exception as e:
            _log.error(f"Failed to setup federation config watcher: {e}")
            self._federation_observer = None

    def process_federation_config(self):
        """Process federation config file changes called by file watcher/observer"""
        # try:
        if not self._federation_config_path.exists():
            _log.debug("Federation config file does not exist, clearing all federation connections")
            self._clear_all_federation_connections()
            return

        with open(self._federation_config_path, 'r') as f:
            config_data = json.load(f)

        _log.debug(f"Processing federation config with {len(config_data)} platforms")

        if 'platforms' in config_data:
            # Compare with current platforms and update connections
            self._update_federation_connections(config_data['platforms'])


    def _update_federation_connections(self, new_platforms_list):
        """Update federation connections based on config changes"""
        # Convert current platforms dict to set of IDs for comparison
        _log.info("_update_federation_connections")

        current_platform_ids = set(self._federation_platforms.keys())

        # Convert new platforms list to dict for easier processing
        new_platforms_dict = {platform['id']: platform for platform in new_platforms_list}
        new_platform_ids = set(new_platforms_dict.keys())

        # Check for duplicate IDs in the list
        if len(new_platforms_list) != len(new_platforms_dict):
            _log.warning("Duplicate platform IDs found in federation config, duplicates will be ignored")

        # Platforms to remove
        to_remove = current_platform_ids - new_platform_ids
        for platform_id in to_remove:
            _log.info(f"Removing federation connection to platform: {platform_id}")
            self._remove_federation_connection(platform_id)

        # Platforms to add or update
        for platform_id, platform_config in new_platforms_dict.items():
            if platform_id not in self._federation_platforms:
                # New platform
                _log.info(f"Adding new federation connection to platform: {platform_id}")
                self._add_federation_connection(platform_id, platform_config)
            else:
                # Check if platform config changed (especially public_credentials)
                current_config = self._federation_platforms[platform_id]
                if self._platform_config_changed(current_config, platform_config):
                    _log.info(f"Platform config changed for {platform_id}, refreshing connection")
                    self._refresh_federation_connection(platform_id, platform_config)

        # Update our tracking - convert list back to dict for internal storage
        self._federation_platforms = new_platforms_dict.copy()

    @staticmethod
    def _platform_config_changed(old_config, new_config):
        """Check if platform configuration has changed"""
        # Check key fields that would require connection refresh
        key_fields = ['address', 'public_credentials', 'group']
        for field in key_fields:
            if old_config.get(field) != new_config.get(field):
                return True
        return False

    def _add_federation_connection(self, platform_id, platform_config):
        """Add a new federation connection"""
        # try:
        # Extract connection details
        address = platform_config['address']
        public_credentials = platform_config['public_credentials']
        group = platform_config.get('group', 'default')

        _log.warning(f"************************Creating federation connection: {platform_id} -> {address}")

        our_creds: VolttronCredentials = self._auth_service.get_credentials(identity=PLATFORM)
        success = self._routing_service.add_external_route(
            platform_id=platform_id,
            address=address,
            public_key=public_credentials,
            our_credentials=our_creds
        )

        if success:
            connection_info = {
                'platform_id': platform_id,
                'address': address,
                'public_credentials': public_credentials,
                'group': group
            }
            self._federation_connections[platform_id] = connection_info
            _log.info(f"Federation connection established to {platform_id} at {address}")
        else:
            _log.error(f"Failed to establish federation connection to {platform_id}")

    def _remove_federation_connection(self, platform_id):
        """Remove a federation connection"""
        # try:
        if hasattr(self, '_routing_service') and self._routing_service:
            self._routing_service.remove_external_route(platform_id)

        if platform_id in self._federation_connections:
            del self._federation_connections[platform_id]

        if platform_id in self._federation_platforms:
            del self._federation_platforms[platform_id]

        _log.info(f"Federation connection removed for platform: {platform_id}")

        # except Exception as e:
        #     _log.error(f"Error removing federation connection for {platform_id}: {e}")

    def _refresh_federation_connection(self, platform_id, new_config):
        """Refresh an existing federation connection"""
        # Remove old connection and add new one
        self._remove_federation_connection(platform_id)
        self._add_federation_connection(platform_id, new_config)

    def _clear_all_federation_connections(self):
        """Clear all federation connections"""
        platform_ids = list([p['id'] for p in self._federation_platforms])
        for platform_id in platform_ids:
            self._remove_federation_connection(platform_id)

    @staticmethod
    def print_call_hierarchy():
        import inspect
        stack = inspect.stack()
        for frame in stack[1:]:  # Skip the current function
            print(f"Function: {frame.function}, File: {frame.filename}, Line: {frame.lineno}")

    def on_connect(self, server_address):
        """Handle server connection and spawn greenlet for publishing cached messages."""

        if server_address in self.cache_publisher_greenlets:
            # Kill existing greenlet (if any) before starting a new one
            self.cache_publisher_greenlets[server_address].kill()

        # Spawn a greenlet to publish cached messages
        _log.info(f"######[FederationService] Spawning cache publisher for {server_address}")
        stop_event = gevent.event.Event()
        self.stop_events[server_address] = stop_event
        greenlet = gevent.spawn(self._publish_messages_from_cache, server_address)
        # Sleep so that spawned greenlet gets a turn
        gevent.sleep(0.1)
        self.cache_publisher_greenlets[server_address] = greenlet

    def _publish_messages_from_cache(self, server_address):
        """Publish messages from cache in batches and cleanly terminate."""

        _log.info(f"######[FederationService] Cache publishing started for {server_address}")
        gevent.sleep(10) # give subscribers timr to start on remote server
        while True:
            if self.stop_events[server_address].is_set():
                _log.info(
                    f"######[FederationService] stop event set for  {server_address}. Terminating cache publisher.")
                return

            # Read up to 10 messages from the cache for this server
            messages = self._routing_service.message_cache.read_from_cache(server_address, 10)

            if not messages:
                # No messages left in cache; terminate the greenlet
                _log.info(
                    f"######[FederationService] No messages left in cache for {server_address}. Terminating cache publisher.")
                return

            # Simulate publishing messages (using Router's send_message method)
            _log.info(f"######[FederationService] Publishing {len(messages)} messages to {server_address}")
            delete_from_cache = []
            for message, cached_time in messages:
                frames:list = json.loads(message)
                msg = frames[-1]
                msg["headers"]["CACHED_TIMESTAMP"] = cached_time
                self._routing_service.send_external(server_address, frames)
                delete_from_cache.append(cached_time)
                if self.stop_events[server_address].is_set():
                    _log.info(
                        f"######[FederationService] stop event set for  {server_address}. exit replay loop.")
                    break
                gevent.sleep(0.2)

            # Delete published messages from cache
            self._routing_service.message_cache.delete_from_cache(server_address, delete_from_cache)

            # Yield control to other greenlets
            gevent.sleep(0.2)

    def on_disconnect(self, server_address):
        """Handle server connection and spawn greenlet for publishing cached messages."""
        self.stop_events[server_address].set()
        self._routing_service.message_cache.flush_to_db()
        _log.info(f"######[FederationService] Setting stop event for {server_address}")

class FederationConfigHandler(FileSystemEventHandler):
    """Handles federation_config.json file changes"""

    def __init__(self, federation_service:FederationService):
        super().__init__()
        self.federation_service = federation_service

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith('federation_config.json'):
            _log.info(f"Federation config modified: {event.src_path}")
            # Add small delay to ensure file write is complete
            gevent.sleep(0.1)
            self.federation_service.process_federation_config()

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('federation_config.json'):
            _log.info(f"Federation config created: {event.src_path}")
            gevent.sleep(0.1)
            self.federation_service.process_federation_config()

