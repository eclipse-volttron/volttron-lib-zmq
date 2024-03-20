# import os

# import sys
# from dataclasses import dataclass, field
# from typing import Optional, Set

# from volttron.services.auth import AuthService
# from volttron.types import ConnectionParameters, ConnectionContext, MessageBusParameters

# @dataclass
# class ZmqConnectionParams(ConnectionParameters):
#     publickey: str
#     secretkey: str
#     serverkey: str

# @dataclass
# class ZmqConnectionContext(ConnectionContext):
#     address: str = None
#     identity: str = None
#     publickey: str = None
#     secretkey: str = None
#     serverkey: str = None
#     agent_uuid: str = None
#     reconnect_interval: int = None

# # Default local address for zmq connections is through ipc.
# local_address = "ipc://%s$VOLTTRON_HOME/run/" % ("@" if sys.platform.startswith("linux") else "") + "vip.socket"
# local_address = local_address.replace("$VOLTTRON_HOME", os.environ.get("VOLTTRON_HOME",
#                                                                        os.path.expanduser("~/.volttron")))

# @dataclass
# class ZmqMessageBusParams(MessageBusParameters):
#     """
#     The ZmqMessageBusParams class has attributes
#     """
#     local_address: str = local_address
#     addresses: Set[str] = field(default_factory=str)
#     auth_service: Optional[AuthService] = None
#     publickey: Optional[str] = None
#     secretkey: Optional[str] = None
#     serverkey: Optional[str] = None
