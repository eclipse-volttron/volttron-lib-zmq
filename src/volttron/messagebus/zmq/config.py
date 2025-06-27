from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from volttron.types import MessageBusConfig, MessageBus


@dataclass
class ZmqMessageBusConfig(MessageBusConfig):
    """
    A data class representing the configuration options for a ZMQ MessageBus.
    
    :ivar volttron_home: The path to the root directory of the Volttron instance.
                         Default is '~/.volttron', which is expanded to the current user's home directory.
    :vartype volttron_home: Union[pathlib.Path, str]
    
    :ivar instance_name: The name of the Volttron instance. Default is the hostname of the machine.
    :vartype instance_name: str
    
    :ivar inproc_address: The inproc address for internal services running within the VOLTTRON process.
                          This is used by services like auth, config store, etc. that run in greenlets.
                          Default is 'inproc://vip'.
    :vartype inproc_address: str
    
    :ivar ipc_address: The IPC address for agents running on the same host but in separate processes.
                       Uses Unix domain sockets for high-performance local communication.
                       Default is 'ipc://@{volttron_home}/run/vip.socket'.
    :vartype ipc_address: str
    
    :ivar addresses: A list of TCP addresses that external agents can connect to from remote machines.
                     These are typically 'tcp://hostname:port' format for network communication.
                     Default is ['tcp://127.0.0.1:22916'].
    :vartype addresses: List[str]
    
    :ivar auth_enabled: Flag indicating whether authentication is enabled for external connections.
                        Internal services via inproc are always trusted.
                        Default is False.
    :vartype auth_enabled: bool

    :ivar log_level: Default logging level. Can be overridden per component.
                     Default is 'INFO'.
    :vartype log_level: str
    
    :ivar log_config: Dictionary containing detailed logging configuration.
                      Default is empty dict.
    :vartype log_config: Dict[str, Any]

    :ivar messagebus_config: Additional configuration specific to the message bus implementation.
                             Default is empty dict.
    :vartype messagebus_config: Dict[str, Any]
    
    """

    # Core identity
    volttron_home: Union[Path, str] = field(default_factory=lambda: Path.home() / ".volttron")
    instance_name: str = field(default_factory=lambda: "volttron-instance")
    auth_enabled: bool = True
    
    # Address configuration - separated by connection type
    inproc_address: str = "inproc://vip"
    ipc_address: str = ""  # Will be generated from volttron_home
    addresses: list[str] = field(default_factory=lambda: ["tcp://127.0.0.1:22916"])
    
    # ZMQ-specific settings
    zmq_context_threads: int = 1
    zmq_max_sockets: int = 1024
    zmq_linger: int = 0

    # Logging specifics.
    log_level: str = "INFO"
    log_config: dict[str, Any] = field(default_factory=dict)
    log_file: Optional[str] = None

    # Additional Configuration
    messagebus_config: dict[str, Any] = field(default_factory=dict)
    agent_monitor_frequency: int = 600

    
    @classmethod
    def get_defaults(cls) -> dict[str, Any]:
        """Return default ZMQ configuration values"""
        import os

        temp_ipc_address = "ipc://@$VOLTTRON_HOME/run/vip.socket"
        if os.getenv("VOLTTRON_HOME"):
            temp_ipc_address = f"ipc://@{os.getenv('VOLTTRON_HOME')}/run/vip.socket"

        
        return {
            "ipc_address": temp_ipc_address,
            "addresses": ["tcp://127.0.0.1:22916"],
            "inproc_address": "inproc://vip",
            "zmq_context_threads": 1,
            "zmq_max_sockets": 1024,
            "zmq_linger": 0,
            "agent_monitor_frequency": 600,
            "auth_enabled": True,
            "log_config": {},
            "log_level": "INFO",
            "messagebus_config": {}
        }
    
    @classmethod
    def create_from_options(cls, options_dict: dict[str, Any]) -> ZmqMessageBusConfig:
        """Create ZMQ config from options dictionary"""
        defaults = cls.get_defaults()
        
        address = options_dict.pop("address", [])
        if isinstance(address, str):
            address = [address]

        if address:
            defaults['addresses'] = address

        ipc_address = options_dict.pop("local_address")
        if ipc_address:
            defaults['ipc_address'] = ipc_address
        
        inproc_address = options_dict.pop('service_address')
        if inproc_address:
            defaults['inproc_address'] = inproc_address
    
        # Federation url is not required, but is available for discovery if not set ahead of time.
        defaults['messagebus_config']['enable_federation'] = options_dict.pop('enable_federation')
        defaults['messagebus_config']['federation_url'] = options_dict.pop('federation_url')

        merged_options = {**defaults, **options_dict}
        
        # Extract required instance_name
        instance_name = merged_options.pop("instance_name")
               
        
        return cls(instance_name=instance_name, **merged_options)
    
    def __post_init__(self):
        """Post-initialization processing"""
        import hashlib
        # Ensure volttron_home is a Path object
        if isinstance(self.volttron_home, str):
            self.volttron_home = Path(self.volttron_home).expanduser().absolute()
        
        # Generate IPC address if not provided
        if not self.ipc_address:
            self.ipc_address = f"ipc://@{self.volttron_home}/run/vip.socket"
        
        # Generate unique inproc address if needed (for multiple instances)
        if self.inproc_address == "inproc://vip" and self.instance_name != "volttron-instance":
            # Create instance-specific inproc address
            instance_hash = hashlib.md5(f"{self.volttron_home}:{self.instance_name}".encode()).hexdigest()[:8]
            self.inproc_address = f"inproc://vip-{instance_hash}"

    def setup_thread_logging(self):
        """Set up logging configuration for thread environment"""
        import logging
        import logging.config
        
        # Configure logging for the thread
        if self.log_config:
            try:
                logging.config.dictConfig(self.log_config)
            except Exception as e:
                # Fallback to basic config
                logging.basicConfig(
                    level=getattr(logging, self.log_level.upper(), logging.INFO),
                    format='%(asctime)s [%(threadName)s] %(name)s(%(lineno)d) %(levelname)s: %(message)s'
                )
        else:
            # Basic logging setup
            log_level = getattr(logging, self.log_level.upper(), logging.INFO)
            
            # Configure for thread-aware logging
            logging.basicConfig(
                level=log_level,
                format='%(asctime)s [%(threadName)s] %(name)s(%(lineno)d) %(levelname)s: %(message)s',
                filename=self.log_file if self.log_file else None
            )
        
    def get_connection_info(self) -> dict[str, Any]:
        """
        Get connection information for different agent types.
        
        :return: Dictionary with connection details for each agent type
        :rtype: Dict[str, Any]
        """
        return {
            "internal_services": {
                "address": self.inproc_address,
                "description": "For services running within VOLTTRON process (greenlets)",
                "authentication_required": False
            },
            "local_agents": {
                "address": self.ipc_address,
                "description": "For agents running on same host in separate processes",
                "authentication_required": self.auth_enabled
            },
            "external_agents": {
                "addresses": self.addresses,
                "description": "For agents connecting from remote machines",
                "authentication_required": self.auth_enabled
            }
        }
