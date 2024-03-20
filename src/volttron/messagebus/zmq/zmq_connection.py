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

import zmq.green as zmq
from volttron.types import Message
from volttron.types.bases import Connection

from .green import Socket as GreenSocket

# TODO ADD BACK rmq
# from volttron.client.vip.rmq_connection import BaseConnection
_log = logging.getLogger(__name__)


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

        self.socket = None
        self.context = zmq_context
        self._identity = self._conn_context.identity
        self._logger = logging.getLogger(__name__)
        self._logger.debug(f"ZMQ connection {self._identity}")

    def connected(self) -> bool:
        ...

    def is_connected(self) -> bool:
        ...

    def send_vip_message(self, message: Message):
        ...

    def recieve_vip_message(self) -> Message:
        ...

    def open_connection(self, type):
        if type == zmq.DEALER:
            self.socket = GreenSocket(self.context)
            if self._identity:
                self.socket.identity = self._identity.encode("utf-8")
        else:
            self.socket = zmq.Socket()

    def set_properties(self, flags):
        hwm = flags.get("hwm", 6000)
        self.socket.set_hwm(hwm)
        reconnect_interval = flags.get("reconnect_interval", None)
        if reconnect_interval:
            self.socket.setsockopt(zmq.RECONNECT_IVL, reconnect_interval)

    def connect(self, callback=None):
        _log.debug(f"connecting to address {self._conn_context.address}")

        self.socket.connect(self._conn_context.address)
        if callback:
            callback(True)

    def bind(self):
        pass

    def register(self, handler):
        self._vip_handler = handler

    def send_vip_object(self, message, flags=0, copy=True, track=False):
        self.socket.send_vip_object(message, flags, copy, track)

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
        self.socket.send_vip(
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
        return self.socket.recv_vip_object(flags, copy, track)

    def disconnect(self):
        self.socket.disconnect(self._url)

    def close_connection(self, linger=5):
        """This method closes ZeroMQ socket"""
        self.socket.close(linger)
        _log.debug("********************************************************************")
        _log.debug("Closing connection to ZMQ: {}".format(self._identity))
        _log.debug("********************************************************************")
