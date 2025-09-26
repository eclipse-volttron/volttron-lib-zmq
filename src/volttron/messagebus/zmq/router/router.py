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

import json
import logging
import os
from pathlib import Path
import sys
import uuid

import gevent

from volttron.messagebus.zmq.routing.federation_service import FederationService


import zmq
from zmq import NOBLOCK, ZMQError


_log = logging.getLogger(__name__)
from volttron.client.known_identities import CONTROL
from volttron.messagebus.zmq.monitor import  Monitor
from volttron.server.server_options import ServerOptions
from volttron.types import MessageBus, MessageBusStopHandler
from volttron.types.auth import AuthService
from volttron.types.peer import ServicePeerNotifier
from volttron.messagebus.zmq.serialize_frames import deserialize_frames, serialize_frames
from volttron.messagebus.zmq.keystore import encode_key, decode_key
from volttron.utils import jsonapi
from volttron.messagebus.zmq.socket import Address


from volttron.server.containers import service_repo
from volttron.messagebus.zmq.routing import (ExternalRPCService, PubSubService, RoutingService)
from volttron.client.known_identities import PLATFORM
from .base_router import ERROR, INCOMING, UNROUTABLE, BaseRouter


class FramesFormatter(object):

    def __init__(self, frames):
        self.frames = frames

    def __repr__(self):
        output = ''
        for f in self.frames:
            output += str(f)
        return output

    __str__ = __repr__


class Router(BaseRouter):
    """Concrete VIP router."""

    def __init__(
            self,
            *,
            server_options: ServerOptions,
            auth_service: AuthService | None = None,
            service_notifier: ServicePeerNotifier | None = None,
            stop_handler: MessageBusStopHandler | None = None,
            zmq_context: zmq.Context | None = None,
            message_bus: MessageBus | None = None
    ):
        from .. import ZmqMessageBus

        super().__init__(
            context=zmq_context,
            default_user_id=server_options.server_messagebus_id,
            service_notifier=service_notifier,
        )
        local_addr = f"ipc://@{server_options.volttron_home.as_posix()}/run/vip.socket"
        self.local_address = Address(local_addr)
        _log.info(f"Local address is: {self.local_address}")
        #self.local_address = Address(server_options.local_address)
        self._addr = server_options.address
        self.addresses = addresses = [Address(addr) for addr in set(server_options.address)]

        # self._secretkey = decode_key(secretkey)
        # self._publickey = decode_key(publickey)
        self.logger = _log
        if self.logger.level == logging.NOTSET:
            self.logger.setLevel(logging.WARNING)

        self._message_bus: ZmqMessageBus = message_bus

        self._monitor = True
        self._tracker = False
        self._instance_name = server_options.instance_name
        self._bind_web_address = None
        self._pubsub = None
        self.ext_rpc = None
        self._msgdebug = "zmq"
        self._message_debugger_socket = None
        self._agent_monitor_frequency = server_options.agent_monitor_frequency
        self._auth_enabled = server_options.auth_enabled
        self._auth_service: AuthService | None = auth_service

        # Initialize RoutingService
        self._routing_service = RoutingService(
            socket=self.socket,
            context=self.context,
            socket_class=self._socket_class,
            poller=self._poller,
            my_addr=self._addr,  # Assuming _addr contains the address
            instance_name=getattr(server_options, 'instance_name', 'default')
        )

        # Initialize PubSubService with routing service
        self.pubsub = PubSubService(
            socket=self.socket, 
            auth_service=self._auth_service, 
            routing_service=self._routing_service
        )

        # Initialize ExternalRPCService
        self.ext_rpc = ExternalRPCService(
            socket=self.socket, 
            routing_service=self._routing_service
        )
        # Federation tracking
        self.federation_service = None
        if server_options.enable_federation and server_options.federation_url:
            self.federation_service = FederationService(
                options=server_options,
                auth_service=self._auth_service,
                routing_service=self._routing_service,
                messagebus=self._message_bus
            )


    def setup(self):

        sock = self.socket
        identity = str(uuid.uuid4())
        sock.identity = identity.encode("utf-8")
        _log.info("ROUTER SOCK identity: {}".format(sock.identity))
        if self._monitor:
            Monitor(sock.get_monitor_socket()).start()
        sock.bind("inproc://vip")
        _log.info("In-process VIP router bound to inproc://vip")
        sock.zap_domain = b"vip"
        addr = self.local_address
        if not addr.identity:
            addr.identity = identity
        if not addr.domain:
            addr.domain = "vip"

        from volttron.types.auth import CredentialsStore
        credential_store: CredentialsStore | None = None

        if self._auth_enabled:
            credential_store = service_repo.resolve(CredentialsStore)
            secretkey = decode_key(credential_store.retrieve_credentials(identity=PLATFORM).secretkey)
            addr.server = "CURVE"
            addr.secretkey = secretkey
            addr.bind(sock)

        _log.info("Local VIP router bound to %s" % addr)
        for address in self.addresses:
            if not address.identity:
                address.identity = identity
            if self._auth_enabled:
                secretkey = decode_key(credential_store.retrieve_credentials(identity=PLATFORM).secretkey)
                address.server = "CURVE"
                address.secretkey = secretkey
            if not address.domain:
                address.domain = "vip"
            address.bind(sock)
            _log.info("Additional VIP router bound to %s" % address)
        
        self.pubsub = PubSubService(self.socket, self._auth_service, self._routing_service) # ._protected_topics, self._ext_routing)
        self.ext_rpc =  None # ExternalRPCService(self.socket, self._ext_routing)
        self._poller.register(sock, zmq.POLLIN)
        _log.info("ZMQ version: {}".format(zmq.zmq_version()))

    def issue(self, topic, frames, extra=None):
        log = self.logger.debug
        formatter = FramesFormatter(frames)
        if topic == ERROR:
            errnum, errmsg = extra
            log("%s (%s): %s", errmsg, errnum, formatter)
        elif topic == UNROUTABLE:
            log("unroutable: %s: %s", extra, formatter)
        else:
            direction = "incoming" if topic == INCOMING else "outgoing"
            if direction == "outgoing":
                log(f"{direction}: {deserialize_frames(frames)}")
            else:
                log(f"{direction}: {frames}")
        if self._tracker:
            self._tracker.hit(topic, frames, extra)
        if self._msgdebug:
            if not self._message_debugger_socket:
                # Initialize a ZMQ IPC socket on which to publish all messages to MessageDebuggerAgent.
                socket_path = os.path.expandvars("$VOLTTRON_HOME/run/messagedebug")
                socket_path = os.path.expanduser(socket_path)
                socket_path = ("ipc://{}".format("@" if sys.platform.startswith("linux") else "") + socket_path)
                self._message_debugger_socket = zmq.Context().socket(zmq.PUB)
                self._message_debugger_socket.connect(socket_path)
            # Publish the routed message, including the "topic" (status/direction), for use by MessageDebuggerAgent.
            frame_bytes = [topic]
            frame_bytes.extend(frames)  # [frame if type(frame) is bytes else frame.bytes for frame in frames])
            frame_bytes = serialize_frames(frames)
            # TODO we need to fix the msgdebugger socket if we need it to be connected
            # frame_bytes = [f.bytes for f in frame_bytes]
            # self._message_debugger_socket.send_pyobj(frame_bytes)

    # This is currently not being used e.g once fixed we won't use it.
    # def extract_bytes(self, frame_bytes):
    #    result = []
    #    for f in frame_bytes:
    #        if isinstance(f, list):
    #            result.extend(self.extract_bytes(f))
    #        else:
    #            result.append(f.bytes)
    #    return result

    def handle_subsystem(self, frames, user_id):
        _log.info(f"Handling subsystem with frames: {frames} user_id: {user_id}")
        gevent.sleep(0.1)
        subsystem = frames[5]
        if subsystem == "quit":
            sender = frames[0]
            # was if sender == 'control' and user_id == self.default_user_id:
            # now we serialize frames and if user_id is always the sender and not
            # recipents.get('User-Id') or default user name
            if sender == CONTROL:
                if self._routing_service:
                    self._routing_service.close_external_connections()
                self.stop()
                raise KeyboardInterrupt()
            else:
                _log.error(f"Sender {sender} not authorized to shutdown platform")
        elif subsystem == "agentstop":
            try:
                drop = frames[6]
                self._drop_peer(drop)
                self._drop_pubsub_peers(drop)
                if self._service_notifier:
                    self._service_notifier.peer_dropped(drop)

                _log.info("ROUTER received agent stop message. dropping peer: {}".format(drop))
            except IndexError:
                _log.error(f"agentstop called but unable to determine agent from frames sent {frames}")
            return False
        elif subsystem == "query":
            try:
                name = frames[6]
            except IndexError:
                value = None
            else:
                if name == "addresses":
                    if self.addresses:
                        value = [addr.base for addr in self.addresses]
                    else:
                        value = [self.local_address.base]
                elif name == "local_address":
                    value = self.local_address.base
                # Allow the agents to know the serverkey.
                elif name == "serverkey":
                    keystore = KeyStore()
                    value = keystore.public
                elif name == "volttron-central-address":
                    value = self._volttron_central_address
                elif name == "volttron-central-serverkey":
                    value = self._volttron_central_serverkey
                elif name == "instance-name":
                    value = self._instance_name
                elif name == "bind-web-address":
                    value = self._bind_web_address
                elif name == "platform-version":
                    raise NotImplementedError()
                    # value = __version__
                elif name == "message-bus":
                    value = os.environ.get("MESSAGEBUS", "zmq")
                elif name == "agent-monitor-frequency":
                    value = self._agent_monitor_frequency
                else:
                    value = None
            frames[6:] = ["", value]
            frames[3] = ""

            return frames
        elif subsystem == "pubsub":
            _log.info(f"Handling pubsub frames {frames} user_id: {user_id}")
            result = self.pubsub.handle_subsystem(frames, user_id)
            return result
        elif subsystem == "routing_table":
            result = self._routing_service.handle_subsystem(frames)
            return result
        elif subsystem == "external_rpc":
            result = self.ext_rpc.handle_subsystem(frames)
            return result

    def _drop_pubsub_peers(self, peer):
        self.pubsub.peer_drop(peer)

    def _add_pubsub_peers(self, peer):
        self.pubsub.peer_add(peer)

    def run(self):
        self._message_bus.set_router_instance(self)
        super().run()

    def poll_sockets(self):
        """
        Poll for incoming messages through router socket or other external socket connections
        """
        try:
            sockets = dict(self._poller.poll())
        except ZMQError as ex:
            _log.error("ZMQ Error while polling: {}".format(ex))

        for sock in sockets:
            if sock == self.socket:
                if sockets[sock] == zmq.POLLIN:
                    frames = sock.recv_multipart(copy=False)
                    if isinstance(frames[0], zmq.Frame):
                        frames = deserialize_frames(frames)
                    _log.info(f"Routing frames {frames}")
                    self.route(frames)
            elif sock in self._routing_service._vip_sockets:
                if sockets[sock] == zmq.POLLIN:
                    _log.info("From Ext Socket: ")
                    self.ext_route(sock)
            elif sock in self._routing_service._monitor_sockets:
                self._routing_service.handle_monitor_event(sock)
            else:
                # _log.info("External ")
                frames = sock.recv_multipart(copy=False)

    def ext_route(self, socket):
        """
        Handler function for message received through external socket connection
        :param socket: socket affected files: {}
        :return:
        """
        # Expecting incoming frames to follow this VIP format:
        #   [SENDER, PROTO, USER_ID, MSG_ID, SUBSYS, ...]
        frames = socket.recv_multipart(copy=False)
        self.route(deserialize_frames(frames))
        for f in frames:
            _log.info("PUBSUBSERVICE Frames: {}".format(bytes(f)))
        if len(frames) < 6:
            return

        sender, proto, user_id, msg_id, subsystem = frames[:5]
        if proto != "VIP1":
            return

        # Handle 'EXT_RPC' subsystem messages
        name = subsystem
        if name == "external_rpc":
            # Reframe the frames
            sender, proto, usr_id, msg_id, subsystem, msg = frames[:6]
            msg_data = jsonapi.loads(msg)
            peer = msg_data["to_peer"]
            # Send to destionation agent/peer
            # Form new frame for local
            frames[:9] = [
                peer,
                sender,
                proto,
                usr_id,
                msg_id,
                "external_rpc",
                msg,
            ]
            try:
                self.socket.send_multipart(frames, flags=NOBLOCK, copy=False)
            except ZMQError as ex:
                _log.info("ZMQ error: {}".format(ex))
                pass
        # Handle 'pubsub' subsystem messages
        elif name == "pubsub":
            if frames[1] == "VIP1":
                recipient = ""
                frames[:1] = ["", ""]
                for f in frames:
                    _log.info("frames: {}".format(bytes(f)))
            result = self.pubsub.handle_subsystem(frames, user_id)
            return result
        # Handle 'routing_table' subsystem messages
        elif name == "routing_table":
            for f in frames:
                _log.info("frames: {}".format(bytes(f)))
            if frames[1] == "VIP1":
                frames[:1] = ["", ""]
            result = self._routing_service.handle_subsystem(frames)
            return result

    def shutdown(self):
        """Enhanced shutdown to clean up federation watcher"""
        try:
            if self.federation_service:
                self.federation_service.shutdown()
                _log.debug("Federation service stopped")
        except Exception as e:
            _log.error(f"Error stopping federation service: {e}")
        
        # Call parent shutdown
        super().shutdown()