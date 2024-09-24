from __future__ import annotations

import json
import logging
import os
import sys
import urllib
import uuid
from errno import EAGAIN, ENOENT, ENOTSOCK
from urllib.parse import parse_qs, urlsplit, urlunsplit

import gevent
import zmq.green as zmq
from volttron.client.decorators import (connection_builder, core_builder, get_server_credentials)
from volttron.client.vip.agent import Core, VIPError
from volttron.server.containers import Unresolvable, service_repo
from volttron.types.agent_context import AgentContext
from volttron.types.auth.auth_credentials import (Credentials, CredentialsStore, PKICredentials)
from volttron.types import AbstractAgent, Connection, CoreLoop
from volttron.types.factories import ConnectionBuilder, CoreBuilder
from zmq import ZMQError

from volttron.messagebus.zmq.zmq_connection import (ZmqConnection, ZmqConnectionContext)
from volttron.client.known_identities import PLATFORM

# from volttron.messagebus.zmq.connection import ZmqConnectionContext
# from volttron.messagebus.zmq.zmq_connection import ZMQConnection
from volttron.utils import get_logger, jsonrpc

_log = get_logger()

__connection_builders__: dict[str, ZmqConnectionBuilder] = {}
__agent_contexts__: dict[str, AgentContext] = {}


@connection_builder
class ZmqConnectionBuilder(ConnectionBuilder):

    def __init__(self, address: str):
        self._address = address

    def build(self, credentials: Credentials) -> Connection:

        context: AgentContext = __agent_contexts__.get(credentials.identity)

        # Not an internal address
        if not self._address.startswith("inproc") and hasattr(credentials, "publickey"):
            if context is not None and context.address is not None:
                address = context.address
            else:
                _log.debug("This maybe a remote agent or a spoofer...I guess")
                address = self._address

            server_creds = get_server_credentials(address=address)

            conn_context = ZmqConnectionContext(address=address,
                                                identity=credentials.identity,
                                                publickey=credentials.publickey,
                                                secretkey=credentials.secretkey,
                                                serverkey=server_creds.publickey)
        else:
            conn_context = ZmqConnectionContext(address=context.address)
        return ZmqConnection(conn_context=conn_context, zmq_context=zmq.Context.instance())


@core_builder
class ZmqCoreBuilder(CoreBuilder):

    def build(self, *, context: AgentContext, owner: AbstractAgent = None) -> CoreLoop:
        __agent_contexts__[context.credentials.identity] = context
        opts = context.options

        try:
            # if the credential store is present then we know we are on the server and
            # can utilize33 the credential store.
            credstore = service_repo.resolve(CredentialsStore)
            server_creds = credstore.retrieve_credentials(identity=PLATFORM)
        except Unresolvable:
            # The exception is where we are a client connecting to the server.  This will
            # allow us to use some context in order to gain access to credentials.
            #
            server_creds = get_server_credentials()

        return ZmqCore(owner=owner,
                       credentials=context.credentials,
                       address=context.address,
                       reconnect_interval=opts.reconnect_interval,
                       agent_uuid=opts.agent_uuid,
                       server_credentials=server_creds)


class ZmqCore(Core):
    """
    Concrete Core class for ZeroMQ message bus
    """

    def __init__(self,
                 owner,
                 address: str = None,
                 credentials: Credentials = None,
                 identity: str = None,
                 reconnect_interval: int = None,
                 server_credentials: Credentials = None,
                 agent_uuid: str = None):
        if credentials is None and identity is None:
            identity = str(uuid.uuid4())
        elif credentials:
            identity = credentials.identity
        # address = "inproc://vip"
        # _log.error(f"ADDRESS hard coded to {address}")
        builder = __connection_builders__.get(address)
        if not builder:
            builder = ZmqConnectionBuilder(address=address)
            __connection_builders__[address] = builder
        super().__init__(owner=owner, credentials=credentials, connection_factory=builder)

        if credentials is None and server_credentials is not None or \
                credentials is not None and server_credentials is None:
            raise ValueError(f"If credentials are specified so should server_credentials {self.__class__.__name__}")

        self.secretkey = None
        self.serverkey = None
        self.publickey = None

        if credentials is None or not isinstance(credentials, PKICredentials):
            _log.warning(f"Authentication mode off for {self.__class__.__name__}")
        else:
            self.publickey = credentials.publickey
            self.secretkey = credentials.secretkey
            self.serverkey = server_credentials.publickey

        if reconnect_interval is None:
            reconnect_interval = 10

        self._connection_context = ZmqConnectionContext(address=address,
                                                        identity=identity,
                                                        publickey=self.publickey,
                                                        secretkey=self.secretkey,
                                                        serverkey=self.serverkey,
                                                        reconnect_interval=reconnect_interval)
        _log.debug(f"ZmqCore Connection Context: {self._connection_context}")
        self.reconnect_interval = reconnect_interval
        self.address = address

        self.identity = identity
        self.agent_uuid = agent_uuid
        self._context = zmq.Context.instance()
        # self._set_keys_from_environment()

        _log.debug(f"AGENT RUNNING on ZMQ Core {self.identity}")
        _log.debug(f"keys: server: {self.serverkey} public: {self.publickey}, secret: {self.secretkey}")
        self._socket = None

    def get_connected(self):
        return super(ZmqCore, self).get_connected()

    def set_connected(self, value):
        # TODO pass through?  Do we need this here?
        #super(ZmqCore, self).set_connected(value)
        pass

    connected = property(get_connected, set_connected)

    def _set_keys_from_environment(self):
        """
        Set public, secret and server keys from the environment onto the connection_params.
        """
        self._set_server_key()
        self._set_public_and_secret_keys()

        if self.publickey and self.secretkey and self.serverkey:
            self._add_keys_to_addr()

    def _add_keys_to_addr(self):
        """Adds public, secret, and server keys to query in VIP address if
        they are not already present"""

        def add_param(query_str, key, value):
            query_dict = parse_qs(query_str)
            if not value or key in query_dict:
                return ""
            # urlparse automatically adds '?', but we need to add the '&'s
            return "{}{}={}".format("&" if query_str else "", key, value)

        url = list(urlsplit(self.address))
        if url[0] in ["tcp", "ipc"]:
            url[3] += add_param(url[3], "publickey", self.publickey)
            url[3] += add_param(url[3], "secretkey", self.secretkey)
            url[3] += add_param(url[3], "serverkey", self.serverkey)
            self.address = str(urlunsplit(url))

    def _set_public_and_secret_keys(self):
        if self.publickey is None or self.secretkey is None:
            creds = json.loads(os.environ.get('VOLTTRON_CREDENTIAL'))
            self.publickey = json.loads(creds['server_credential'])['public']  # os.environ.get("AGENT_PUBLICKEY")
            self.secretkey = json.loads(creds['server_credential'])['secret']  # os.environ.get("AGENT_SECRETKEY")
            _log.debug(f"after setting agent private and public key {self.publickey} {self.secretkey}")
        if self.publickey is None or self.secretkey is None:
            self.publickey, self.secretkey, _ = self._get_keys_from_addr()
        if self.publickey is None or self.secretkey is None:
            self.publickey, self.secretkey = self._get_keys_from_keystore()

    def _set_server_key(self):
        if self.serverkey is None:
            _log.debug(f"environ keys: {dict(os.environ).keys()}")
            _log.debug(f"server key from env {os.environ.get('VOLTTRON_SERVERKEY')}")
            creds = json.loads(os.environ.get('VOLTTRON_SERVER_CREDENTIAL'))

            self.serverkey = json.loads(creds['server_credential'])['public']  # os.environ.get("VOLTTRON_SERVERKEY")

        # TODO: This needs to move somewhere else that is not zmq dependent some mapping between host and creds.
        known_serverkey = self.serverkey
        # known_serverkey = self._get_serverkey_from_known_hosts()
        #
        # if (self._connection_context.serverkey is not None and known_serverkey is not None
        #         and self._connection_context.serverkey != known_serverkey):
        #     raise Exception("Provided server key ({}) for {} does "
        #                     "not match known serverkey ({}).".format(self._connection_context.serverkey,
        #                                                              self.address,
        #                                                              known_serverkey))

        # Until we have containers for agents we should not require all
        # platforms that connect to be in the known host file.
        # See issue https://github.com/VOLTTRON/volttron/issues/1117
        if known_serverkey is not None:
            self.serverkey = known_serverkey

    def _get_serverkey_from_known_hosts(self):
        known_hosts_file = f"{cc.get_volttron_home()}/known_hosts"
        known_hosts = KnownHostsStore(known_hosts_file)
        return known_hosts.serverkey(self.address)

    def _get_keys_from_addr(self):
        url = list(urlsplit(self.address))
        query = parse_qs(url[3])
        publickey = query.get("publickey", [None])[0]
        secretkey = query.get("secretkey", [None])[0]
        serverkey = query.get("serverkey", [None])[0]
        return publickey, secretkey, serverkey

    def loop(self, running_event):
        """
        Concrete implementation of an event loop.
        :param running_event:
        :return:
        """
        # pre-setup
        # self.context.set(zmq.MAX_SOCKETS, 30690)
        # self.connection = ZMQConnection(self.address,
        #                                 self.identity,
        #                                 self._instance_name,
        #                                 context=self._context)
        self._connection = ZmqConnection(self._connection_context, self._context)

        self._connection.open_connection(zmq.DEALER)

        flags = dict(hwm=6000, reconnect_interval=self.reconnect_interval)
        self._connection.set_properties(flags)
        self._socket = self._connection._socket
        yield

        # pre-start
        state = type("HelloState", (), {"count": 0, "ident": None})

        hello_response_event = gevent.event.Event()
        connection_failed_check, hello, hello_response = self.create_event_handlers(state, hello_response_event,
                                                                                    running_event)

        def close_socket(sender):
            gevent.sleep(2)
            try:
                if self._socket is not None:
                    self._socket.monitor(None, 0)
                    self._socket.close(1)
            finally:
                self._socket = None

        def monitor():
            # Call socket.monitor() directly rather than use
            # get_monitor_socket() so we can use green sockets with
            # regular contexts (get_monitor_socket() uses
            # self.context.socket()).
            from zmq.utils.monitor import recv_monitor_message

            addr = "inproc://monitor.v-%d" % (id(self._socket),)
            _log.debug(f"Monitor socket {addr}")
            sock = None
            if self._socket is not None:
                try:
                    self._socket.monitor(addr)
                    sock = zmq.Socket(self._context, zmq.PAIR)

                    sock.connect(addr)
                    while True:
                        try:
                            message = recv_monitor_message(sock)
                            self.onsockevent.send(self, **message)
                            event = message["event"]
                            if event & zmq.EVENT_CONNECTED:
                                hello()
                            elif event & zmq.EVENT_DISCONNECTED:
                                self.connected = False
                            elif event & zmq.EVENT_CONNECT_RETRIED:
                                self._reconnect_attempt += 1
                                if self._reconnect_attempt == 50:
                                    self.connected = False
                                    sock.disable_monitor()
                                    self.stop()
                                    self.ondisconnected.send(self)
                            elif event & zmq.EVENT_MONITOR_STOPPED:
                                break
                        except ZMQError as exc:
                            if exc.errno == ENOTSOCK:
                                break

                except ZMQError as exc:
                    raise
                    # if exc.errno == EADDRINUSE:
                    #     pass
                finally:
                    try:
                        url = list(urllib.parse.urlsplit(self.address))
                        if url[0] in ["tcp"] and sock is not None:
                            sock.close()
                        if self._socket is not None:
                            self._socket.monitor(None, 0)
                    except Exception as exc:
                        _log.debug("Error in closing the socket: {}".format(exc))

        self.onconnected.connect(hello_response)
        self.ondisconnected.connect(close_socket)

        if self.address[:4] in ["tcp:", "ipc:"]:
            self.spawn(monitor).join(0)
        self._connection.connect()

        # TODO: Why is it only inprox that we are calling hello?  This is what it was
        # if self.address.startswith("inproc:"):
        #     hello()
        hello()

        def vip_loop():
            _log.debug("VIP LOOP STARTED!")
            sock = self._socket
            while True:
                try:
                    # Message at this point in time will be a
                    # volttron.client.vip.socket.Message object that has attributes
                    # for all of the vip elements.  Note these are no longer bytes.
                    # see https://github.com/volttron/volttron/issues/2123
                    message = self._connection.recv_vip_object(copy=False)
                    #message = sock.recv_vip_object(copy=False)
                    _log.debug(f"Detected incoming message {message}")
                except ZMQError as exc:

                    if exc.errno == EAGAIN:
                        continue
                    elif exc.errno == ENOTSOCK:
                        self._socket = None
                        break
                    else:
                        raise
                subsystem = message.subsystem
                _log.debug(f"New Message: {message}")
                #_log.debug(f"New message:\n\t{pformat(message.__dict__, 2)}")
                #_log.debug(f"New Message Received:\n\tsubsystem: {subsystem}\n\tid: {message.id}\n\targs length: {len(message.args)}\n\targ[0]: {message.args[0]}")
                # _log.debug("Received new message {0}, {1}, {2}, {3}".format(subsystem, message.id, len(message.args),
                #                                                             message.args[0]))
                #_log.debug(f"Message is: {message}")

                # Handle hellos sent by CONNECTED event
                if (str(subsystem) == "hello" and message.id == state.ident and len(message.args) > 3
                        and message.args[0] == "welcome"):
                    version, server, identity = message.args[1:4]
                    assert self._connection.connected
                    self.onconnected.send(self, version=version, router=server, identity=identity)
                    continue

                try:
                    handle = self.subsystems[subsystem]
                except KeyError:
                    _log.error(
                        "peer %r requested unknown subsystem %r",
                        message.peer,
                        subsystem,
                    )
                    message.user = ""
                    message.args = list(router._INVALID_SUBSYSTEM)
                    message.args.append(message.subsystem)
                    message.subsystem = "error"
                    sock.send_vip_object(message, copy=False)
                else:
                    # We don't want to exit based upon a server issue, however from a client it may be good for
                    # us to exit the program based upon a failure.
                    if subsystem == 'error':
                        if not os.environ.get("VOLTTRON_SERVER"):
                            # We now are going to make specific error messages be cleaned up and exit
                            # note this is only for clients not server agents as noted above.
                            #
                            # Example message.args is
                            # [-32001, 'Peer: listener not authorized to subscribe to ', '', 'pubsub']
                            #
                            # jsonrpc.UNAUTHORIZED is -32001
                            if message.args[0] == jsonrpc.UNAUTHORIZED:
                                handle(message)
                                _log.error(f"{message.args}")
                                sys.exit(jsonrpc.UNAUTHORIZED)
                                return

                    handle(message)

        _log.debug(f"Starting vip_loop for {self.identity}")
        yield gevent.spawn(vip_loop)
        # pre-stop
        yield
        # pre-finish
        try:
            self.connection.disconnect()
            self._socket.monitor(None, 0)
            self.connection.close_connection(1)
        except AttributeError:
            pass
        except ZMQError as exc:
            if exc.errno != ENOENT:
                _log.exception("disconnect error")
        finally:
            self._socket = None
        yield


if __name__ == '__main__':
    a = object()
    core = ZmqCore()
    zmq_con = ZmqConnectionContext()
    assert zmq_con

# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2022 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}

# import heapq
# import inspect
# import logging
# import os
# import signal
# import threading
# import time
# import urllib.parse
# import uuid
# import warnings
# import weakref
# from contextlib import contextmanager
# from errno import ENOENT
# from urllib.parse import urlsplit, parse_qs, urlunsplit

# import gevent.event
# from gevent.queue import Queue
# from zmq import green as zmq
# from zmq.green import ZMQError, EAGAIN, ENOTSOCK
# from zmq.utils.monitor import recv_monitor_message

# from volttron.utils import ClientContext as cc, get_address

# # from volttron.client.agent import utils
# # from volttron.client.agent.utils import load_platform_config, get_platform_instance_name
# # TODO add back rabbitmq
# # from volttron.client.keystore import KeyStore, KnownHostsStore
# # from volttron.utils.rmq_mgmt import RabbitMQMgmt
# from volttron import utils
# from volttron.utils.keystore import KeyStore, KnownHostsStore
# from .decorators import annotate, annotations, dualmethod
# from .dispatch import Signal
# from .errors import VIPError
# # from .. import router

# # TODO add back rabbitmq
# # from ..rmq_connection import RMQConnection
# from volttron.utils.socket import Message
# from ...vip.zmq_connection import ZMQConnection
# import volttron.client as client

# __all__ = ["BasicCore", "Core", "ZMQCore", "killing"]

# if cc.is_rabbitmq_available():
#     import pika

#     __all__.append("RMQCore")

# _log = logging.getLogger(__name__)

# class Periodic(object):    # pylint: disable=invalid-name
#     """Decorator to set a method up as a periodic callback.

#     The decorated method will be called with the given arguments every
#     period seconds while the agent is executing its run loop.
#     """

#     def __init__(self, period, args=None, kwargs=None, wait=0):
#         """Store period (seconds) and arguments to call method with."""
#         assert period > 0
#         self.period = period
#         self.args = args or ()
#         self.kwargs = kwargs or {}
#         self.timeout = wait

#     def __call__(self, method):
#         """Attach this object instance to the given method."""
#         annotate(method, list, "core.periodics", self)
#         return method

#     def _loop(self, method):
#         # pylint: disable=missing-docstring
#         # Use monotonic clock provided on hu's loop instance.
#         now = gevent.get_hub().loop.now
#         period = self.period
#         deadline = now()
#         if self.timeout != 0:
#             timeout = self.timeout or period
#             deadline += timeout
#             gevent.sleep(timeout)
#         while True:
#             try:
#                 method(*self.args, **self.kwargs)
#             except (Exception, gevent.Timeout):
#                 _log.exception("unhandled exception in periodic callback")
#             deadline += period
#             timeout = deadline - now()
#             if timeout > 0:
#                 gevent.sleep(timeout)
#             else:
#                 # Prevent catching up.
#                 deadline -= timeout

#     def get(self, method):
#         """Return a Greenlet for the given method."""
#         return gevent.Greenlet(self._loop, method)

# class ScheduledEvent(object):
#     """Class returned from Core.schedule."""

#     def __init__(self, function, args=None, kwargs=None):
#         self.function = function
#         self.args = args or []
#         self.kwargs = kwargs or {}
#         self.canceled = False
#         self.finished = False

#     def cancel(self):
#         """Mark the timer as canceled to avoid a callback."""
#         self.canceled = True

#     def __call__(self):
#         if not self.canceled:
#             self.function(*self.args, **self.kwargs)
#         self.finished = True

# def findsignal(obj, owner, name):
#     parts = name.split(".")
#     if len(parts) == 1:
#         signal = getattr(obj, name)
#     else:
#         signal = owner
#         for part in parts:
#             signal = getattr(signal, part)
#     assert isinstance(signal, Signal), "bad signal name %r" % (name, )
#     return signal

# class BasicCore(object):
#     delay_onstart_signal = False
#     delay_running_event_set = False

#     def __init__(self, owner):
#         self.greenlet = None
#         self.spawned_greenlets = weakref.WeakSet()
#         self._async = None
#         self._async_calls = []
#         self._stop_event = None
#         self._schedule_event = None
#         self._schedule = []
#         self.onsetup = Signal()
#         self.onstart = Signal()
#         self.onstop = Signal()
#         self.onfinish = Signal()
#         self.oninterrupt = None
#         self.tie_breaker = 0

#         # TODO: HAndle sig int for child process
#         # latest gevent does not have gevent.signal_handler()
#         # TODO - update based on latest gevent function location
#         # SIGINT does not work in Windows.
#         # If using the standalone agent on a windows machine,
#         # this section will be skipped
#         # if python_platform.system() != "Windows":
#         #     gevent.signal_handler(signal.SIG_IGN, self._on_sigint_handler)
#         #     gevent.signal_handler(signal.SIG_DFL, self._on_sigint_handler)
#         # prev_int_signal = gevent.signal_handler(signal.SIGINT)
#         # # To avoid a child agent handler overwriting the parent agent handler
#         # if prev_int_signal in [None, signal.SIG_IGN, signal.SIG_DFL]:
#         #     self.oninterrupt = gevent.signal_handler(
#         #         signal.SIGINT, self._on_sigint_handler
#         #     )
#         self._owner = owner

#     def setup(self):
#         # Split out setup from __init__ to give oportunity to add
#         # subsystems with signals
#         try:
#             owner = self._owner
#         except AttributeError:
#             return
#         del self._owner
#         periodics = []

#         def setup(member):    # pylint: disable=redefined-outer-name
#             periodics.extend(
#                 periodic.get(member) for periodic in annotations(member, list, "core.periodics"))
#             for deadline, args, kwargs in annotations(member, list, "core.schedule"):
#                 self.schedule(deadline, member, *args, **kwargs)
#             for name in annotations(member, set, "core.signals"):
#                 findsignal(self, owner, name).connect(member, owner)

#         inspect.getmembers(owner, setup)

#         def start_periodics(sender, **kwargs):    # pylint: disable=unused-argument
#             for periodic in periodics:
#                 sender.spawned_greenlets.add(periodic)
#                 periodic.start()
#             del periodics[:]

#         self.onstart.connect(start_periodics)

#     def loop(self, running_event):
#         # pre-setup
#         yield
#         # pre-start
#         yield
#         # pre-stop
#         yield
#         # pre-finish
#         yield

#     def link_receiver(self, receiver, sender, **kwargs):
#         greenlet = gevent.spawn(receiver, sender, **kwargs)
#         self.spawned_greenlets.add(greenlet)
#         return greenlet

#     def run(self, running_event=None):    # pylint: disable=method-hidden
#         """Entry point for running agent."""

#         self._schedule_event = gevent.event.Event()
#         self.setup()
#         self.greenlet = current = gevent.getcurrent()

#         def kill_leftover_greenlets():
#             for glt in self.spawned_greenlets:
#                 glt.kill()

#         self.greenlet.link(lambda _: kill_leftover_greenlets())

#         def handle_async_():
#             """Execute pending calls."""
#             calls = self._async_calls
#             while calls:
#                 func, args, kwargs = calls.pop()
#                 greenlet = gevent.spawn(func, *args, **kwargs)
#                 self.spawned_greenlets.add(greenlet)

#         def schedule_loop():
#             heap = self._schedule
#             event = self._schedule_event
#             cur = gevent.getcurrent()
#             now = time.time()
#             while True:
#                 if heap:
#                     deadline = heap[0][0]
#                     timeout = min(5.0, max(0.0, deadline - now))
#                 else:
#                     timeout = None
#                 if event.wait(timeout):
#                     event.clear()
#                 now = time.time()
#                 while heap and now >= heap[0][0]:
#                     _, _, callback = heapq.heappop(heap)
#                     greenlet = gevent.spawn(callback)
#                     cur.link(lambda glt: greenlet.kill())

#         self._stop_event = stop = gevent.event.Event()
#         self._async = gevent.get_hub().loop.async_()
#         self._async.start(handle_async_)
#         current.link(lambda glt: self._async.stop())

#         looper = self.loop(running_event)
#         next(looper)
#         self.onsetup.send(self)

#         loop = next(looper)
#         if loop:
#             self.spawned_greenlets.add(loop)
#         scheduler = gevent.Greenlet(schedule_loop)
#         if loop:
#             loop.link(lambda glt: scheduler.kill())
#         self.onstart.connect(lambda *_, **__: scheduler.start())
#         if not self.delay_onstart_signal:
#             self.onstart.sendby(self.link_receiver, self)
#         if not self.delay_running_event_set:
#             if running_event is not None:
#                 running_event.set()
#         try:
#             if loop and loop in gevent.wait([loop, stop], count=1):
#                 raise RuntimeError("VIP loop ended prematurely")
#             stop.wait()
#         except (gevent.GreenletExit, KeyboardInterrupt):
#             pass

#         scheduler.kill()
#         next(looper)
#         receivers = self.onstop.sendby(self.link_receiver, self)
#         gevent.wait(receivers)
#         next(looper)
#         self.onfinish.send(self)

#     def stop(self, timeout=None):

#         def halt():
#             self._stop_event.set()
#             self.greenlet.join(timeout)
#             return self.greenlet.ready()

#         if gevent.get_hub() is self._stop_event.hub:
#             return halt()

#         return self.send_async(halt).get()

#     def _on_sigint_handler(self, signo, *_):
#         """
#         Event handler to set onstop event when the agent needs to stop
#         :param signo:
#         :param _:
#         :return:
#         """
#         _log.debug("SIG interrupt received. Calling stop")
#         if signo == signal.SIGINT:
#             self._stop_event.set()
#             # self.stop()

#     def send(self, func, *args, **kwargs):
#         self._async_calls.append((func, args, kwargs))
#         self._async.send()

#     def send_async(self, func, *args, **kwargs):
#         result = gevent.event.AsyncResult()
#         async_ = gevent.hub.get_hub().loop.async_()
#         results = [None, None]

#         def receiver():
#             async_.stop()
#             exc, value = results
#             if exc is None:
#                 result.set(value)
#             else:
#                 result.set_exception(exc)

#         async_.start(receiver)

#         def worker():
#             try:
#                 results[:] = [None, func(*args, **kwargs)]
#             except Exception as exc:    # pylint: disable=broad-except
#                 results[:] = [exc, None]
#             async_.send()

#         self.send(worker)
#         return result

#     def spawn(self, func, *args, **kwargs):
#         assert self.greenlet is not None
#         greenlet = gevent.spawn(func, *args, **kwargs)
#         self.spawned_greenlets.add(greenlet)
#         return greenlet

#     def spawn_later(self, seconds, func, *args, **kwargs):
#         assert self.greenlet is not None
#         greenlet = gevent.spawn_later(seconds, func, *args, **kwargs)
#         self.spawned_greenlets.add(greenlet)
#         return greenlet

#     def spawn_in_thread(self, func, *args, **kwargs):
#         result = gevent.event.AsyncResult()

#         def wrapper():
#             try:
#                 self.send(result.set, func(*args, **kwargs))
#             except Exception as exc:    # pylint: disable=broad-except
#                 self.send(result.set_exception, exc)

#         result.thread = thread = threading.Thread(target=wrapper)
#         thread.daemon = True
#         thread.start()
#         return result

#     @dualmethod
#     def periodic(self, period, func, args=None, kwargs=None, wait=0):
#         warnings.warn(
#             "Use of the periodic() method is deprecated in favor of the "
#             "schedule() method with the periodic() generator. This "
#             "method will be removed in a future version.",
#             DeprecationWarning,
#         )
#         greenlet = Periodic(period, args, kwargs, wait).get(func)
#         self.spawned_greenlets.add(greenlet)
#         greenlet.start()
#         return greenlet

#     @periodic.classmethod
#     def periodic(cls, period, args=None, kwargs=None, wait=0):    # pylint: disable=no-self-argument
#         warnings.warn(
#             "Use of the periodic() decorator is deprecated in favor of "
#             "the schedule() decorator with the periodic() generator. "
#             "This decorator will be removed in a future version.",
#             DeprecationWarning,
#         )
#         return Periodic(period, args, kwargs, wait)

#     @classmethod
#     def receiver(cls, signal):

#         def decorate(method):
#             annotate(method, set, "core.signals", signal)
#             return method

#         return decorate

#     @dualmethod
#     def schedule(self, deadline, func, *args, **kwargs):
#         event = ScheduledEvent(func, args, kwargs)
#         try:
#             it = iter(deadline)
#         except TypeError:
#             self._schedule_callback(deadline, event)
#         else:
#             self._schedule_iter(it, event)
#         return event

#     def get_tie_breaker(self):
#         self.tie_breaker += 1
#         return self.tie_breaker

#     def _schedule_callback(self, deadline, callback):
#         deadline = utils.get_utc_seconds_from_epoch(deadline)
#         heapq.heappush(self._schedule, (deadline, self.get_tie_breaker(), callback))
#         if self._schedule_event:
#             self._schedule_event.set()

#     def _schedule_iter(self, it, event):

#         def wrapper():
#             if event.canceled:
#                 event.finished = True
#                 return
#             try:
#                 deadline = next(it)
#             except StopIteration:
#                 event.function(*event.args, **event.kwargs)
#                 event.finished = True
#             else:
#                 self._schedule_callback(deadline, wrapper)
#                 event.function(*event.args, **event.kwargs)

#         try:
#             deadline = next(it)
#         except StopIteration:
#             event.finished = True
#         else:
#             self._schedule_callback(deadline, wrapper)

#     @schedule.classmethod
#     def schedule(cls, deadline, *args, **kwargs):    # pylint: disable=no-self-argument
#         if hasattr(deadline, "timetuple"):
#             # deadline = time.mktime(deadline.timetuple())
#             deadline = utils.get_utc_seconds_from_epoch(deadline)

#         def decorate(method):
#             annotate(method, list, "core.schedule", (deadline, args, kwargs))
#             return method

#         return decorate

# class Core(BasicCore):
#     # We want to delay the calling of "onstart" methods until we have
#     # confirmation from the server that we have a connection. We will fire
#     # the event when we hear the response to the hello message.
#     delay_onstart_signal = True
#     # Agents started before the router can set this variable
#     # to false to keep from blocking. AuthSerookce does this.
#     delay_running_event_set = True

#     def __init__(
#         self,
#         owner,
#         address=None,
#         identity=None,
#         context=None,
#         publickey=None,
#         secretkey=None,
#         serverkey=None,
#         volttron_home=os.path.abspath(cc.get_volttron_home()),
#         agent_uuid=None,
#         reconnect_interval=None,
#         version="0.1",
#         _instance_name=None,
#         messagebus=None,
#     ):
#         self.volttron_home = volttron_home

#         # These signals need to exist before calling super().__init__()
#         self.onviperror = Signal()
#         self.onsockevent = Signal()
#         self.onconnected = Signal()
#         self.ondisconnected = Signal()
#         self.configuration = Signal()
#         super(Core, self).__init__(owner)
#         self.address = address if address is not None else get_address()
#         self.identity = str(identity) if identity is not None else str(uuid.uuid4())
#         self.agent_uuid = agent_uuid
#         self.publickey = publickey
#         self.secretkey = secretkey
#         self.serverkey = serverkey
#         self.reconnect_interval = reconnect_interval
#         self._reconnect_attempt = 0
#         self._instance_name = _instance_name
#         self.messagebus = messagebus
#         self.subsystems = {"error": self.handle_error}
#         self.__connected = False
#         self._version = version
#         self.socket = None
#         self.connection = None

#         _log.debug("address: %s", address)
#         _log.debug("identity: %s", self.identity)
#         _log.debug("agent_uuid: %s", agent_uuid)
#         _log.debug("serverkey: %s", serverkey)

#     def version(self):
#         return self._version

#     def get_connected(self):
#         return self.__connected

#     def set_connected(self, value):
#         self.__connected = value

#     connected = property(
#         fget=lambda self: self.get_connected(),
#         fset=lambda self, v: self.set_connected(v),
#     )

#     def stop(self, timeout=None, platform_shutdown=False):
#         # Send message to router that this agent is stopping
#         if self.__connected and not platform_shutdown:
#             frames = [self.identity]
#             self.connection.send_vip("", "agentstop", args=frames, copy=False)
#         super(Core, self).stop(timeout=timeout)

#     # This function moved directly from the zmqcore agent.  it is included here because
#     # when we are attempting to connect to a zmq bus from a rmq bus this will be used
#     # to create the public and secret key for that connection or use it if it was already
#     # created.
#     def _get_keys_from_keystore(self):
#         """Returns agent's public and secret key from keystore"""
#         if self.agent_uuid:
#             # this is an installed agent, put keystore in its agent's directory
#             if self.identity is None:
#                 raise ValueError("Agent's VIP identity is not set")
#             keystore_dir = os.path.join(self.volttron_home, "agents", self.identity)
#         else:
#             if not self.volttron_home:
#                 raise ValueError("VOLTTRON_HOME must be specified.")
#             keystore_dir = os.path.join(self.volttron_home, "keystores", self.identity)

#         keystore_path = os.path.join(keystore_dir, "keystore.json")
#         keystore = KeyStore(keystore_path)
#         return keystore.public, keystore.secret

#     def register(self, name, handler, error_handler=None):
#         self.subsystems[name] = handler
#         if error_handler:
#             name_bytes = name

#             def onerror(sender, error, **kwargs):
#                 if error.subsystem == name_bytes:
#                     error_handler(sender, error=error, **kwargs)

#             self.onviperror.connect(onerror)

#     def handle_error(self, message):
#         if len(message.args) < 4:
#             _log.debug("unhandled VIP error %s", message)
#         elif self.onviperror:
#             args = message.args
#             error = VIPError.from_errno(*args)
#             self.onviperror.send(self, error=error, message=message)

#     def create_event_handlers(self, state, hello_response_event, running_event):

#         def connection_failed_check():
#             # If we don't have a verified connection after 10.0 seconds
#             # shut down.
#             if hello_response_event.wait(10.0):
#                 return
#             _log.error("No response to hello message after 10 seconds.")
#             _log.error("Type of message bus used {}".format(self.messagebus))
#             _log.error("A common reason for this is a conflicting VIP IDENTITY.")
#             _log.error("Another common reason is not having an auth entry on"
#                        "the target instance.")
#             _log.error("Shutting down agent.")
#             _log.error("Possible conflicting identity is: {}".format(self.identity))

#             self.stop(timeout=10.0)

#         def hello():
#             # Send hello message to VIP router to confirm connection with
#             # platform
#             state.ident = ident = "connect.hello.%d" % state.count
#             state.count += 1
#             self.spawn(connection_failed_check)
#             message = Message(peer="", subsystem="hello", id=ident, args=["hello"])
#             self.connection.send_vip_object(message)

#         def hello_response(sender, version="", router="", identity=""):
#             _log.info(f"Connected to platform: identity: {identity} version: {version}")
#             _log.debug("Running onstart methods.")
#             hello_response_event.set()
#             self.onstart.sendby(self.link_receiver, self)
#             self.configuration.sendby(self.link_receiver, self)
#             if running_event is not None:
#                 running_event.set()

#         return connection_failed_check, hello, hello_response

# class ZMQCore(Core):
#     """
#     Concrete Core class for ZeroMQ message bus
#     """

#     def __init__(
#         self,
#         owner,
#         address=None,
#         identity=None,
#         context=None,
#         publickey=None,
#         secretkey=None,
#         serverkey=None,
#         volttron_home=None,
#         agent_uuid=None,
#         reconnect_interval=None,
#         version="0.1",
#         _instance_name=None,
#         messagebus="zmq",
#     ):
#         if volttron_home is None:
#             volttron_home = cc.get_volttron_home()

#         super(ZMQCore, self).__init__(
#             owner,
#             address=address,
#             identity=identity,
#             context=context,
#             publickey=publickey,
#             secretkey=secretkey,
#             serverkey=serverkey,
#             volttron_home=volttron_home,
#             agent_uuid=agent_uuid,
#             reconnect_interval=reconnect_interval,
#             version=version,
#             _instance_name=_instance_name,
#             messagebus=messagebus,
#         )
#         self.context = context or zmq.Context.instance()
#         self.messagebus = messagebus
#         self._set_keys()

#         _log.debug("AGENT RUNNING on ZMQ Core {}".format(self.identity))
#         _log.debug(
#             f"keys: server: {self.serverkey} public: {self.publickey}, secret: {self.secretkey}")
#         self.socket = None

#     def get_connected(self):
#         return super(ZMQCore, self).get_connected()

#     def set_connected(self, value):
#         super(ZMQCore, self).set_connected(value)

#     connected = property(get_connected, set_connected)

#     def _set_keys(self):
#         """Implements logic for setting encryption keys and putting
#         those keys in the parameters of the VIP address
#         """
#         self._set_server_key()
#         self._set_public_and_secret_keys()

#         if self.publickey and self.secretkey and self.serverkey:
#             self._add_keys_to_addr()

#     def _add_keys_to_addr(self):
#         """Adds public, secret, and server keys to query in VIP address if
#         they are not already present"""

#         def add_param(query_str, key, value):
#             query_dict = parse_qs(query_str)
#             if not value or key in query_dict:
#                 return ""
#             # urlparse automatically adds '?', but we need to add the '&'s
#             return "{}{}={}".format("&" if query_str else "", key, value)

#         url = list(urlsplit(self.address))
#         if url[0] in ["tcp", "ipc"]:
#             url[3] += add_param(url[3], "publickey", self.publickey)
#             url[3] += add_param(url[3], "secretkey", self.secretkey)
#             url[3] += add_param(url[3], "serverkey", self.serverkey)
#             self.address = str(urlunsplit(url))

#     def _set_public_and_secret_keys(self):
#         if self.publickey is None or self.secretkey is None:
#             self.publickey = os.environ.get("AGENT_PUBLICKEY")
#             self.secretkey = os.environ.get("AGENT_SECRETKEY")
#             _log.debug(
#                 f" after setting agent provate and public key {self.publickey} {self.secretkey}")
#         if self.publickey is None or self.secretkey is None:
#             self.publickey, self.secretkey, _ = self._get_keys_from_addr()
#         if self.publickey is None or self.secretkey is None:
#             self.publickey, self.secretkey = self._get_keys_from_keystore()

#     def _set_server_key(self):
#         if self.serverkey is None:
#             _log.debug(f" environ keys: {dict(os.environ).keys()}")
#             _log.debug(f"server key from env {os.environ.get('VOLTTRON_SERVERKEY')}")
#             self.serverkey = os.environ.get("VOLTTRON_SERVERKEY")
#         known_serverkey = self._get_serverkey_from_known_hosts()

#         if (self.serverkey is not None and known_serverkey is not None
#                 and self.serverkey != known_serverkey):
#             raise Exception("Provided server key ({}) for {} does "
#                             "not match known serverkey ({}).".format(self.serverkey, self.address,
#                                                                      known_serverkey))

#         # Until we have containers for agents we should not require all
#         # platforms that connect to be in the known host file.
#         # See issue https://github.com/VOLTTRON/volttron/issues/1117
#         if known_serverkey is not None:
#             self.serverkey = known_serverkey

#     def _get_serverkey_from_known_hosts(self):
#         known_hosts_file = f"{cc.get_volttron_home()}/known_hosts"
#         known_hosts = KnownHostsStore(known_hosts_file)
#         return known_hosts.serverkey(self.address)

#     def _get_keys_from_addr(self):
#         url = list(urlsplit(self.address))
#         query = parse_qs(url[3])
#         publickey = query.get("publickey", [None])[0]
#         secretkey = query.get("secretkey", [None])[0]
#         serverkey = query.get("serverkey", [None])[0]
#         return publickey, secretkey, serverkey

# @contextmanager
# def killing(greenlet, *args, **kwargs):
#     """Context manager to automatically kill spawned greenlets.

#     Allows one to kill greenlets that would continue after a timeout:

#         with killing(agent.vip.pubsub.subscribe(
#                 'peer', 'topic', callback)) as subscribe:
#             subscribe.get(timeout=10)
#     """
#     try:
#         yield greenlet
#     finally:
#         greenlet.kill(*args, **kwargs)

# if cc.is_rabbitmq_available():

#     class RMQCore(Core):
#         """
#         Concrete Core class for RabbitMQ message bus
#         """

#         def __init__(
#             self,
#             owner,
#             address=None,
#             identity=None,
#             context=None,
#             publickey=None,
#             secretkey=None,
#             serverkey=None,
#             volttron_home=os.path.abspath(client.get_home()),
#             agent_uuid=None,
#             reconnect_interval=None,
#             version="0.1",
#             _instance_name=None,
#             messagebus="rmq",
#             volttron_central_address=None,
#             volttron_central_instance_name=None,
#         ):
#             super(RMQCore, self).__init__(
#                 owner,
#                 address=address,
#                 identity=identity,
#                 context=context,
#                 publickey=publickey,
#                 secretkey=secretkey,
#                 serverkey=serverkey,
#                 volttron_home=volttron_home,
#                 agent_uuid=agent_uuid,
#                 reconnect_interval=reconnect_interval,
#                 version=version,
#                 _instance_name=_instance_name,
#                 messagebus=messagebus,
#             )
#             self.volttron_central_address = volttron_central_address

#             # TODO Look at this and see if we really need this here.
#             # if _instance_name is specified as a parameter in this calls it will be because it is
#             # a remote connection. So we load it from the platform configuration file
#             if not _instance_name:
#                 self._instance_name = cc.get_instance_name()
#             else:
#                 self._instance_name = _instance_name

#             assert (self._instance_name
#                     ), "Instance name must have been set in the platform config file."
#             assert (not volttron_central_instance_name
#                     ), "Please report this as volttron_central_instance_name shouldn't be passed."

#             # self._event_queue = gevent.queue.Queue
#             self._event_queue = Queue()

#             self.rmq_user = ".".join([self._instance_name, self.identity])

#             _log.debug("AGENT RUNNING on RMQ Core {}".format(self.rmq_user))

#             self.messagebus = messagebus
#             self.rmq_mgmt = RabbitMQMgmt()
#             self.rmq_address = address
#             # added so that it is available to auth subsytem when connecting
#             # to remote instance
#             if self.publickey is None or self.secretkey is None:
#                 self.publickey, self.secretkey = self._get_keys_from_keystore()

#         def _get_keys_from_addr(self):
#             return None, None, None

#         def get_connected(self):
#             return super(RMQCore, self).get_connected()

#         def set_connected(self, value):
#             super(RMQCore, self).set_connected(value)

#         connected = property(get_connected, set_connected)

#         def _build_connection_parameters(self):
#             param = None

#             if self.identity is None:
#                 raise ValueError("Agent's VIP identity is not set")
#             else:
#                 try:
#                     if self._instance_name == cc.get_instance_name():
#                         param = self.rmq_mgmt.build_agent_connection(self.identity,
#                                                                      self._instance_name)
#                     else:
#                         param = self.rmq_mgmt.build_remote_connection_param(
#                             self.rmq_user, self.rmq_address, True)
#                 except AttributeError:
#                     _log.error("RabbitMQ broker may not be running. Restart the broker first")
#                     param = None

#             return param

#         def loop(self, running_event):
#             if not isinstance(self.rmq_address, pika.ConnectionParameters):
#                 self.rmq_address = self._build_connection_parameters()
#             # pre-setup
#             self.connection = RMQConnection(
#                 self.rmq_address,
#                 self.identity,
#                 self._instance_name,
#                 reconnect_delay=self.rmq_mgmt.rmq_config.reconnect_delay(),
#                 vc_url=self.volttron_central_address,
#             )
#             yield

#             # pre-start
#             flags = dict(durable=False, exclusive=True, auto_delete=True)
#             if self.connection:
#                 self.connection.set_properties(flags)
#                 # Register callback handler for VIP messages
#                 self.connection.register(self.vip_message_handler)

#             state = type("HelloState", (), {"count": 0, "ident": None})
#             hello_response_event = gevent.event.Event()
#             connection_failed_check, hello, hello_response = self.create_event_handlers(
#                 state, hello_response_event, running_event)

#             def connection_error():
#                 self.connected = False
#                 self.stop()
#                 self.ondisconnected.send(self)

#             def connect_callback():
#                 router_connected = False
#                 try:
#                     bindings = self.rmq_mgmt.get_bindings("volttron")
#                 except AttributeError:
#                     bindings = None
#                 router_user = router_key = "{inst}.{ident}".format(inst=self._instance_name,
#                                                                    ident="router")
#                 if bindings:
#                     for binding in bindings:
#                         if (binding["destination"] == router_user
#                                 and binding["routing_key"] == router_key):
#                             router_connected = True
#                             break
#                 # Connection retry attempt issue #1702.
#                 # If the agent detects that RabbitMQ broker is reconnected before the router, wait
#                 # for the router to connect before sending hello()
#                 if router_connected:
#                     hello()
#                 else:
#                     _log.debug(
#                         "Router not bound to RabbitMQ yet, waiting for 2 seconds before sending hello {}"
#                         .format(self.identity))
#                     self.spawn_later(2, hello)

#             # Connect to RMQ broker. Register a callback to get notified when
#             # connection is confirmed
#             if self.rmq_address:
#                 self.connection.connect(connect_callback, connection_error)

#             self.onconnected.connect(hello_response)
#             self.ondisconnected.connect(self.connection.close_connection)

#             def vip_loop():
#                 if self.rmq_address:
#                     wait_period = 1    # 1 second
#                     while True:
#                         message = None
#                         try:
#                             message = self._event_queue.get(wait_period)
#                         except gevent.Timeout:
#                             pass
#                         except Exception as exc:
#                             _log.error(exc.args)
#                             raise
#                         if message:
#                             subsystem = message.subsystem

#                             if subsystem == "hello":
#                                 if (subsystem == "hello" and message.id == state.ident
#                                         and len(message.args) > 3
#                                         and message.args[0] == "welcome"):
#                                     version, server, identity = message.args[1:4]
#                                     self.connected = True
#                                     self.onconnected.send(
#                                         self,
#                                         version=version,
#                                         router=server,
#                                         identity=identity,
#                                     )
#                                     continue
#                             try:
#                                 handle = self.subsystems[subsystem]
#                             except KeyError:
#                                 _log.error(
#                                     "peer %r requested unknown subsystem %r",
#                                     message.peer,
#                                     subsystem,
#                                 )
#                                 message.user = ""
#                                 message.args = list(router._INVALID_SUBSYSTEM)
#                                 message.args.append(message.subsystem)
#                                 message.subsystem = "error"
#                                 self.connection.send_vip_object(message)
#                             else:
#                                 handle(message)

#             yield gevent.spawn(vip_loop)
#             # pre-stop
#             yield
#             # pre-finish
#             if self.rmq_address:
#                 self.connection.close_connection()
#             yield

#         def vip_message_handler(self, message):
#             # _log.debug("RMQ VIP Core {}".format(message))
#             self._event_queue.put(message)
