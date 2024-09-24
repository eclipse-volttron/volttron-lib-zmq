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
#
# This material was prepared as an account of work sponsored by an agency of
# the United States Government. Neither the United States Government nor the
# United States Department of Energy, nor Battelle, nor any of their
# employees, nor any jurisdiction or organization that has cooperated in the
# development of these materials, makes any warranty, express or
# implied, or assumes any legal liability or responsibility for the accuracy,
# completeness, or usefulness or any information, apparatus, product,
# software, or process disclosed, or represents that its use would not infringe
# privately owned rights. Reference herein to any specific commercial product,
# process, or service by trade name, trademark, manufactufrer, or otherwise
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
import logging
from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import parse_qs, urlsplit, urlunsplit

import zmq.green as zmq
from volttron.types import Connection, Message, Identity
from zmq.utils.monitor import recv_monitor_message

from .green import Socket as GreenSocket

from volttron.messagebus.zmq import get_logger
from .serialize_frames import deserialize_frames

_log = get_logger()

@dataclass
class ZmqConnectionContext:
    address: Optional[str] = None
    identity: Optional[str] = None
    publickey: Optional[str] = None
    secretkey: Optional[str] = None
    serverkey: Optional[str] = None
    agent_uuid: Optional[str] = None
    reconnect_interval: Optional[int] = None


class ZmqConnection(Connection):
    """
    Maintains ZMQ socket connection
    """

    def __init__(self, conn_context: ZmqConnectionContext, zmq_context: zmq.Context):
        super().__init__()
        self._conn_context = conn_context

        self._socket: zmq.Socket | GreenSocket | None = None
        self._zmq_context: zmq.Context = zmq_context
        self._identity: Identity = self._conn_context.identity
        self._logger = logging.getLogger(__name__)
        self._logger.debug(f"ZMQ connection {self._identity}")

    @property
    def connected(self) -> bool:
        return self._socket is not None

    # def connect(self):
    #     self.open_connection(type=zmq.DEALER)

    def disconnect(self):
        self.close_connection()
        self._socket = None

    def is_connected(self) -> bool:
        return self.connected

    def send_vip_message(self, message: Message):
        assert isinstance(message, Message)
        self.send_vip_object(message=message)

    def receive_vip_message(self) -> Message:
        _log.debug(f"Waiting for message recv")
        return self.recv_vip_object()

    def open_connection(self, type=None):
        if type == zmq.DEALER:
            self._socket = GreenSocket(self._zmq_context)
            if self._identity:
                self._socket.identity = self._identity.encode("utf-8")
        else:
            self._socket = zmq.Socket()

    def set_properties(self, flags):
        hwm = flags.get("hwm", 6000)
        self._socket.set_hwm(hwm)
        reconnect_interval = flags.get("reconnect_interval", None)
        if reconnect_interval:
            self._socket.setsockopt(zmq.RECONNECT_IVL, reconnect_interval)

    def connect(self, callback=None):

        def _add_keys_to_addr() -> str:
            '''Adds public, secret, and server keys to query in VIP address if
            they are not already present'''

            def add_param(query_str, key, value):
                query_dict = parse_qs(query_str)
                if not value or key in query_dict:
                    return ''
                # urlparse automatically adds '?', but we need to add the '&'s
                return '{}{}={}'.format('&' if query_str else '', key, value)

            url = list(urlsplit(self._conn_context.address))

            if url[0] in ['tcp', 'ipc']:
                url[3] += add_param(url[3], 'publickey', self._conn_context.publickey)
                url[3] += add_param(url[3], 'secretkey', self._conn_context.secretkey)
                url[3] += add_param(url[3], 'serverkey', self._conn_context.serverkey)

            return str(urlunsplit(url))

        from volttron.messagebus.zmq.keystore import decode_key
        addr = _add_keys_to_addr()
        _log.debug(f"connecting to address {addr}")
        # client.curve_secretkey = decode_key(cred_key_store["secretkey"])
        # client.curve_publickey = decode_key(cred_key_store["publickey"])
        # client.curve_serverkey = decode_key(server_public_key)
        # self._socket.curve_secretkey = decode_key(self._conn_context.secretkey)
        # self._socket.curve_publickey = decode_key(self._conn_context.publickey)
        # self._socket.curve_serverkey = decode_key(self._conn_context.serverkey)
        self._socket.connect(addr=addr)
        if callback:
            callback(True)

    def bind(self):
        pass

    def register(self, handler):
        self._vip_handler = handler

    def send_vip_object(self, message: Message, flags: int = 0, copy: bool = True, track: bool = False):
        assert self._socket
        self._socket.send_vip_object(message, flags, copy, track)

    def send_vip(
            self,
            peer,
            subsystem,
            args=None,
            msg_id: bytes = b"",
            user=b"",
            via=None,
            flags=0,
            copy=True,
            track=False,
    ):
        _log.debug(f"ZmqConnection.send_vip: {peer}, {subsystem}, {args}, {msg_id}, {user}, {via}, {flags}, {copy}, {track}")
        self._socket.send_vip(
            peer,
            subsystem,
            args=args,
            msg_id=msg_id,
            user=user,
            via=via,
            flags=flags,
            copy=copy,
            track=track,
        )

    def recv_vip_object(self, flags=0, copy=True, track=False):
        obj: Message = self._socket.recv_vip_object(flags, copy, track)

        if obj.args:
            obj.args = deserialize_frames(obj.args)

        return obj

    def disconnect(self):
        self._socket.disconnect(self._url)

    def close_connection(self, linger=5):
        """This method closes ZeroMQ socket"""
        self._socket.close(linger)
        _log.debug("********************************************************************")
        _log.debug("Closing connection to ZMQ: {}".format(self._identity))
        _log.debug("********************************************************************")
