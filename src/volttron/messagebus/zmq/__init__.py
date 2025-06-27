# -*- coding: utf-8 -*- {{{
# vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:
#
# Copyright 2020, Battelle Memorial Institute.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# green
# This material was prepared as an account of work sponsored by an agency of
# the United States Government. Neither the United States Government nor the
# United States Department of Energy, nor Battelle, nor any of their
# employees, nor any jurisdiction or organization that has cooperated in the
# development of these materials, makes any warranty, express or
# implied, or assumes any legal liability or responsibility for the accuracy,
# completeness, or usefulness or any information, apparatus, product,
# software, or process disclosed, or represents that its use would not infringe
# privately owned rights. Reference herein to any specific commercial product,
# process, or service by trade name, trademark, manufacturer, or otherwise
# does not necessarily constitute or imply its endorsement, recommendation, or
# favoring by the United States Government or any agency thereof, or
# Battelle Memorial Institute. The views and opinions of authors expressed
# herein do not necessarily state or reflect those of the
# United States Government or any agency thereof.
#
# PACIFIC NORTHWEST NATIONAL LABORATORY operated by
# BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# under Contract DE-AC05-76RL01830
# }}}
"""VIP - VOLTTRON™ Interconnect Protocol implementation

# See https://volttron.readthedocs.io/en/develop/core_services/messagebus/VIP/VIP-Overview.html
# for protocol specification.

# This module is useful for using VIP outside of gevent. Please understand
# that ZeroMQ sockets are not thread-safe and care must be used when using
# across threads (or avoided all together). There is no locking around the
# state as there is with the gevent version in the green sub-module.
# """
from __future__ import annotations

import logging
import threading
from typing import Optional

from volttron.messagebus.zmq.config import ZmqMessageBusConfig
from volttron.messagebus.zmq.federation_bridge import ZmqFederationBridge
from volttron.types.federation import FederationBridge
import zmq.green as zmq
from volttron.messagebus.zmq.router import Router
from volttron.messagebus.zmq.zmq_connection import ZmqConnection
from volttron.messagebus.zmq.zmq_core import ZmqCore
# from volttron.client.vip.agent.core import Core
from volttron.server.decorators import service
from volttron.server.server_options import ServerOptions
from volttron.types import Message, MessageBus, MessageBusStopHandler
from volttron.types.auth import AuthService
from volttron.types.peer import ServicePeerNotifier

_log = logging.getLogger(__name__)


# TOP level zmq context for the router is here.
zmq_context: zmq.Context = zmq.Context()

# Main loops
# def zmq_router(opts: argparse.Namespace, notifier, tracker, protected_topics,
#                external_address_file, stop):
def zmq_router(server_options: ServerOptions,
               auth_service: AuthService = None,
               notifier: ServicePeerNotifier = None,
               stop_handler: MessageBusStopHandler = None,
               zmq_context: zmq.Context = None,
               messsage_bus: MessageBus = None):
               # , notifier, tracker, protected_topics,
               # external_address_file, stop):
    try:
        _log.debug("Running zmq router")
        # _log.debug(f"Opts: {opts}")
        # _log.debug(f"Notifier: {notifier}")
        # _log.debug(f"Tracker: {tracker}")
        # _log.debug(f"Protected Topics: {protected_topics}")
        # _log.debug(f"External Address: {external_address_file}")
        # _log.debug(f"Stop: {stop}")
        router = Router(
            server_options=server_options,
            auth_service=auth_service,
            service_notifier=notifier,
            stop_handler=stop_handler,
            zmq_context=None,
            message_bus=messsage_bus
        )
        router.run()
    except Exception as ex:
        _log.error("Unhandled exceeption from router thread.")
        _log.exception(ex)
        raise
    except KeyboardInterrupt:
        pass
    finally:
        _log.debug("In finally")
        if stop_handler is not None:
            stop_handler.message_bus_shutdown()


@service
class ZmqMessageBus(MessageBus):
    from volttron.types.auth.auth_service import AuthService

    def __init__(self, server_options: ServerOptions,
                 auth_service: AuthService | None = None,
                 notifier: ServicePeerNotifier | None = None
                 ):
                 # opts: argparse.Namespace,
                 # notifier,
                 # tracker,
                 # protected_topics,
                 # external_address_file,
                 # stop):

        # cred_service = service_repo.resolve(CredentialsStore)
        # server_creds = cred_service.retrieve_credentials(identity="server")
        # if credentials_store is not None:
        #     creds = credentials_store.retrieve_credentials(identity=PLATFORM)
        #     self._publickey = creds.publickey
        #     self._secretkey = creds.secretkey

        self._server_options = server_options
        self._config = self._server_options.get_messagebus_config()
        self._auth_service = auth_service
        #self._opts = opts
        self._notifier = notifier
        #self._tracker = tracker
        #self._protected_topics = protected_topics
        #self._external_address_file = external_address_file
        #self._stop = stop
        self._thread = None
        self._federation_bridge: FederationBridge | None = None

        from queue import Queue
        self._router_task_queue = Queue()
        self._router_result_queue = Queue()
        self._router_instance = None

    def start(self):
        import os
        env = os.environ.copy()

        if gevent_support := env.get("GEVENT_SUPPORT") == "True":
            del os.environ["GEVENT_SUPPORT"]
        self._thread = threading.Thread(target=zmq_router,
                                        daemon=True,
                                        args=[
                                            self._server_options,
                                            self._auth_service,
                                            self._notifier,
                                            self._stop_handler,
                                            zmq_context,
                                            self
                                        ])
        # self._notifier, self._tracker,
        # self._protected_topics, self._external_address_file, self._stop
        self._thread.start()
        if gevent_support:
            os.environ["GEVENT_SUPPORT"] = "True"

    def set_router_instance(self, router):
        """
        Set the router instance reference for direct communication
        
        :param router: Router instance to reference
        """
        self._router_instance = router

    def is_running(self):
        return self._thread.is_alive()

    def stop(self):
        if self._stop_handler is not None:
            self._stop_handler.message_bus_shutdown()
    
    def create_federation_bridge(self) -> Optional[FederationBridge]:
        """
        Create a federation bridge for this message bus.
        
        :return: A ZmqFederationBridge instance or None if federation is disabled
        :rtype: Optional[FederationBridge]
        """
        if not self._config:
            return None
            
        # Check if federation is enabled in config
        messagebus_config = getattr(self._config, 'messagebus_config', {})
        if not messagebus_config.get("enable_federation", False):
            return None
        
        # Verify thread safety is working
        self._check_thread_safety()
            
        if self._federation_bridge is None:
            self._federation_bridge = ZmqFederationBridge(self)
            
        return self._federation_bridge

    def execute_in_router_thread(self, fn, timeout=5):
        """
        Execute a function in the router's thread safely
        
        :param fn: Function to execute (no arguments)
        :param timeout: Timeout in seconds
        :return: Result of the function execution
        :raises: TimeoutError if execution times out
                 Any exception raised by the function
        """
        if not self._thread or not self._thread.is_alive():
            raise RuntimeError("Router thread not running")
            
        if not self._router_instance:
            raise RuntimeError("Router instance not available")
        
        import uuid
        import queue
        
        # Create unique task ID
        task_id = str(uuid.uuid4())
        
        # Queue the task
        self._router_task_queue.put((task_id, fn))
        
        # Wait for result with timeout
        try:
            result_id, result_value, result_exception = self._router_result_queue.get(timeout=timeout)
            
            if result_id != task_id:
                _log.error(f"Router thread execution error: got result for wrong task (expected {task_id}, got {result_id})")
                raise RuntimeError(f"Router thread execution synchronization error")
                
            if result_exception is not None:
                raise result_exception
                
            return result_value
            
        except queue.Empty:
            _log.error(f"Router thread execution timed out after {timeout}s")
            raise TimeoutError(f"Router thread execution timed out after {timeout} seconds")
        
    def send_vip_message(self, message: Message):
        ...

    def receive_vip_message(self) -> Message:
        ...
    def _check_thread_safety(self):
        """Check if execute_in_router_thread is properly functioning"""
        if not self._thread or not self._thread.is_alive():
            _log.warning("Router thread not running, thread safety check skipped")
            return False
        
        if not self._router_instance:
            _log.warning("Router instance not available, thread safety check skipped")
            return False
            
        try:
            # Try to execute a simple function in router thread
            result = self.execute_in_router_thread(lambda: "thread_safety_check_ok")
            thread_safe = (result == "thread_safety_check_ok")
            if thread_safe:
                _log.debug("Thread safety check passed: execute_in_router_thread is working")
            else:
                _log.error(f"Thread safety check failed: unexpected result: {result}")
            return thread_safe
        except Exception as e:
            _log.error(f"Thread safety check failed with error: {e}")
            return False
        
def register_zmq_messagebus():
    """Register ZMQ messagebus with the global registry"""
    import json
    import os
    from pathlib import Path
    
    # Determine registry path
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        registry_dir = Path(xdg_config) / "volttron"
    else:
        registry_dir = Path.home() / ".local" / "share" / "volttron"
    
    registry_dir.mkdir(parents=True, exist_ok=True)
    registry_file = registry_dir / "messagebus_registry.json"
    
    # Load existing registry or create new
    registry_data = {}
    if registry_file.exists():
        try:
            with open(registry_file) as f:
                registry_data = json.load(f)
        except Exception:
            pass  # Start fresh if corrupted
    
    # Register Zmq messagebus
    registry_data["zmq"] = ".".join([ZmqMessageBusConfig.__module__, ZmqMessageBusConfig.__name__])
    
    # Write updated registry
    with open(registry_file, 'w') as f:
        json.dump(registry_data, f, indent=2)

register_zmq_messagebus()

__all__: list[str] = ['ZmqConnection', 'ZmqCore']
