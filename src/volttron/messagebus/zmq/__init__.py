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

import argparse
import bisect
import logging
import random
import sys
import threading
import uuid
from pathlib import Path
from threading import local as _local
from typing import Optional

import gevent
import zmq as _zmq
import zmq.green as zmq
from gevent.local import local

import volttron.messagebus.zmq.zap
from volttron.client.known_identities import PLATFORM
# from volttron.client.vip.agent.core import Core
from volttron.server.containers import service_repo
from volttron.server.decorators import service
from volttron.server.server_options import ServerOptions
from volttron.types.auth import AuthService
from volttron.types.auth.auth_credentials import (Credentials, CredentialsCreator, CredentialsStore)
from volttron.types import Message, MessageBus, MessageBusStopHandler
from volttron.types.peer import ServicePeerNotifier

from volttron.messagebus.zmq.router import Router
from volttron.messagebus.zmq.zmq_connection import ZmqConnection
from volttron.messagebus.zmq.zmq_core import ZmqCore
from volttron.client.known_identities import PLATFORM

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
               zmq_context: zmq.Context = None):
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
        Router(
            server_options=server_options,
            auth_service=auth_service,
            service_notifier=notifier,
            stop_handler=stop_handler,
            zmq_context=zmq_context
            #local_address=server_options.local_address,
            #addresses=server_options.address,
            #default_user_id="vip.service",
            #service_notifier=notifier,
            #monitor=opts.monitor,
            #tracker=tracker,
            #instance_name=server_options.instance_name, #.instance_name,
            #protected_topics=protected_topics,
            #external_address_file=external_address_file,
            #msgdebug=opts.msgdebug,
            #service_notifier=notifier,
            #auth_enabled=server_options.auth_enabled,

        ).run()
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
    from volttron.types.auth.auth_credentials import CredentialsStore
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
        super().__init__()

        self._server_options = server_options
        self._auth_service = auth_service
        #self._opts = opts
        self._notifier = notifier
        #self._tracker = tracker
        #self._protected_topics = protected_topics
        #self._external_address_file = external_address_file
        #self._stop = stop
        self._thread = None
        self._router_instance = None
        self._stop_handler = None
        self._startup_error = None
        self._startup_event = threading.Event()
        self._shutdown_event = threading.Event()

    def start(self):
        """Start the ZMQ message bus"""
        if self.is_running():
            _log.debug("Message bus already running")
            return
            
        _log.debug("Starting ZMQ message bus")
        self._startup_error = None
        self._startup_event.clear()
        self._shutdown_event.clear()
        
        import os
        env = os.environ.copy()
        gevent_support = env.get("GEVENT_SUPPORT") == "True"
        if gevent_support:
            del os.environ["GEVENT_SUPPORT"]
        
        def router_wrapper():
            try:
                _log.debug("Router thread starting")
                self._startup_event.set()
                
                # Create stop handler that signals shutdown
                class ThreadStopHandler:
                    def __init__(self, shutdown_event):
                        self.shutdown_event = shutdown_event
                    
                    def message_bus_shutdown(self):
                        _log.debug("Router shutdown requested")
                        self.shutdown_event.set()
                
                # Set the stop handler
                if self.get_stop_handler() is None:
                    self.set_stop_handler(ThreadStopHandler(self._shutdown_event))
                
                zmq_router(
                    self._server_options,
                    self._auth_service,
                    self._notifier,
                    self.get_stop_handler()
                )
            except Exception as e:
                _log.error(f"Router thread failed: {e}")
                self._startup_error = e
                raise
            finally:
                _log.debug("Router thread ending")
                self._shutdown_event.set()
        
        self._thread = threading.Thread(target=router_wrapper, daemon=True)
        self._thread.start()
        
        if gevent_support:
            os.environ["GEVENT_SUPPORT"] = "True"
        
        _log.debug("ZMQ message bus thread started")

    def create_federation_bridge(self) -> FederationBridge:
        """
        Create a ZMQ-specific federation bridge
        
        :return: Federation bridge implementation
        """
        router = self._get_router_instance()
        if not router:
            raise ValueError("Router instance is not available. Cannot create federation bridge.")
        return ZmqFederationBridge(
            router=router,
            auth_service=self._auth_service
        )



    def _get_router_instance(self) -> Router:
        """
        Get the Router instance for this message bus.
        This is needed by the federation bridge to access the routing service.
        
        :return: Router instance
        :raises ValueError: If router instance is not available
        """
        if hasattr(self, '_router_instance') and self._router_instance is not None:
            return self._router_instance
        else:
            raise ValueError("Router instance not available")
        
    def is_running(self):
        """Check if ZMQ message bus is running"""
        if self._thread is None:
            return False
        
        is_alive = self._thread.is_alive()
        
        # If thread died, check if there was a startup error
        if not is_alive and self._startup_error:
            _log.error(f"Message bus thread failed with error: {self._startup_error}")
        
        return is_alive
    
    def wait_for_startup(self, timeout=5.0):
        """Wait for the message bus to start up"""
        return self._startup_event.wait(timeout)
    
    def get_startup_error(self):
        """Get any startup error that occurred"""
        return self._startup_error

    def stop(self):
        """Stop the ZMQ message bus"""
        _log.debug("Stopping ZMQ message bus")
        
        if not self.is_running():
            _log.debug("Message bus not running, nothing to stop")
            return
        
        # Signal shutdown
        if self.get_stop_handler() is not None:
            try:
                self.get_stop_handler().message_bus_shutdown()
            except Exception as e:
                _log.error(f"Error calling stop handler: {e}")
        
        # Wait for graceful shutdown
        shutdown_ok = self._shutdown_event.wait(timeout=3.0)
        if not shutdown_ok:
            _log.warning("Router did not shutdown gracefully")
        
        # Wait for thread to finish
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                _log.warning("Message bus thread did not stop cleanly")
            else:
                _log.debug("Message bus thread stopped")
            self._thread = None

    def send_vip_message(self, message: Message):
        ...

    def receive_vip_message(self) -> Message:
        ...


#from volttron.types.federation import FederationBridge
#from volttron.messagebus.zmq.federation import ZmqFederationBridge

__all__: list[str] = ['ZmqConnection', 'ZmqCore']
