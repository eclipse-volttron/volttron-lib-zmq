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

See https://volttron.readthedocs.io/en/develop/core_services/messagebus/VIP/VIP-Overview.html
for protocol specification.

This module is useful for using VIP outside of gevent. Please understand
that ZeroMQ sockets are not thread-safe and care must be used when using
across threads (or avoided all together). There is no locking around the
state as there is with the gevent version in the green sub-module.
"""

import bisect
import dataclasses
import random
import uuid
from copy import copy
import logging
from dataclasses import field, dataclass
from pathlib import Path
from typing import List, Optional

import gevent
import zmq.green as zmq

from volttron.platform.curve import encode_key
from volttron.services.auth import AuthService
from volttron.types import MessageBusInterface, MessageBusParameters, AgentFactory, Credentials, CredentialsGenerator, \
    CredentialsManager

from volttron.messagebus.zmq.connection import ZmqMessageBusParams
from volttron.messagebus.zmq.router import Router

from volttron.messagebus.zmq.zmq_core import ZmqCore

from threading import local as _local

import zmq as _zmq

from volttron.messagebus.zmq.socket import _Socket
#from volttron.utils.socket import _Socket
from volttron.platform.curve.keystore import KeyStore
from volttron.types.message import Message
from volttron.utils import serialize_frames

_log = logging.getLogger(__name__)
_log.setLevel(logging.DEBUG)

class Socket(_Socket, _zmq.Socket):
    _context_class = _zmq.Context
    _local_class = _local


class ZmqAgentFactory(AgentFactory):
    pass


class ZmqCredentialGenerator(CredentialsGenerator):
    @staticmethod
    def generate(identity: str) -> Credentials:
        ks = KeyStore()
        return Credentials(identity=identity, type="CURVE", credentials=ks.generate_keypair_dict())


class ZmqMessageBus(MessageBusInterface):
    def __init__(self):
        super().__init__()
        self._auth_service = None
        self._zap_socket = None
        self._zap_greenlet = None
        # self._auth_service: AuthService = None
        # self._server_credentials: Credentials = None
        # self._service_credentials: Credentials = None
        self._zmq_thread: threading.Thread = None
        self._credential_manager: CredentialsManager = None



    # def get_server_credentials(self) -> Credentials:
    #     if not self._server_credentials:
    #         raise ValueError("Initialize object before attempting to get Credentials.")
    #
    #     return self._server_credentials
    #
    # def get_service_credentials(self) -> Credentials:
    #     if not self._service_credentials:
    #         raise ValueError("Initialize object before attempting to get service credentials.")
    #
    #     return self._service_credentials

    # def initialize(self, **kwargs):
    #     """
    #     Initialize the message bus so that it's ready to run after setting up the
    #     auth service or whatever is necessary.
    #
    #     :param opts:
    #     :return:
    #     """
    #
    #     cred_store = Path(f"{opts.volttron_home}/credential_store")
    #     cred_store.mkdir(exist_ok=True)
    #     server_cred = cred_store.joinpath("server.json")
    #     service_cred = cred_store.joinpath("service.json")
    #
    #     if not server_cred.exists():
    #         # Initialize the credentials that are required to connect to as the platform.
    #         ks = KeyStore(f"{server_cred}")
    #         self._server_credentials = Credentials("platform", "CURVE", ks)
    #
    #     if not service_cred.exists():
    #         # Initialize the credentials that are required to connect to as the platform.
    #         ks = KeyStore(f"{service_cred}")
    #         self._service_credentials = Credentials("platform", "CURVE", ks)
    #
    #     self._secretkey = ks.secret
    #     self._opts = opts


    @staticmethod
    def get_default_parameters() -> MessageBusParameters:
        return ZmqMessageBusParams()

    def _start_zap(self):
        self.zap_socket = zmq.Socket(zmq.Context.instance(), zmq.ROUTER)
        self.zap_socket.bind("inproc://zeromq.zap.01")

    def _stop_zap(self):
        if self._zap_greenlet is not None:
            self._zap_greenlet.kill()

        if self.zap_socket is not None:
            self.zap_socket.unbind("inproc://zeromq.zap.01")

    def _zap_loop(self):
        """
                The zap loop is the starting of the authentication process for
                the VOLTTRON zmq message bus.  It talks directly with the low
                level socket so all responses must be byte like objects, in
                this case we are going to send zmq frames across the wire.

                :param sender:
                :param kwargs:
                :return:
                """
        self._is_connected = True
        self._zap_greenlet = gevent.getcurrent()
        sock = self.zap_socket
        time = gevent.core.time
        blocked = {}
        wait_list = []
        timeout = None
        # if self.core.messagebus == "rmq":
        #     # Check the topic permissions of all the connected agents
        #     self._check_rmq_topic_permissions()
        # else:

        #self._send_protected_update_to_pubsub(self._protected_topics)

        while True:
            events = sock.poll(timeout)
            now = time()
            if events:
                zap = sock.recv_multipart()

                version = zap[2]
                if version != b"1.0":
                    continue
                domain, address, userid, kind = zap[4:8]
                credentials = zap[8:]
                if kind == b"CURVE":
                    credentials[0] = encode_key(credentials[0])
                elif kind not in [b"NULL", b"PLAIN"]:
                    continue
                response = zap[:4]
                domain = domain.decode("utf-8")
                address = address.decode("utf-8")
                kind = kind.decode("utf-8")
                user = self._auth_service.authenticate(domain, address, kind, credentials)
                _log.debug(f"AUTH: After authenticate user id: {user}, {userid}")
                if user:
                    _log.info(
                        "authentication success: userid=%r domain=%r, address=%r, "
                        "mechanism=%r, credentials=%r, user=%r",
                        userid,
                        domain,
                        address,
                        kind,
                        credentials[:1],
                        user,
                    )
                    response.extend([b"200", b"SUCCESS", user.encode("utf-8"), b""])
                    sock.send_multipart(response)
                else:
                    userid = str(uuid.uuid4())
                    _log.info(
                        "authentication failure: userid=%r, domain=%r, address=%r, "
                        "mechanism=%r, credentials=%r",
                        userid,
                        domain,
                        address,
                        kind,
                        credentials,
                    )
                    # TODO SETUP MODE????
                    # If in setup mode, add/update auth entry
                    # if self._setup_mode:
                    #     self._update_auth_entry(domain, address, kind, credentials[0], userid)
                    #     _log.info(
                    #         "new authentication entry added in setup mode: domain=%r, address=%r, "
                    #         "mechanism=%r, credentials=%r, user_id=%r",
                    #         domain,
                    #         address,
                    #         kind,
                    #         credentials[:1],
                    #         userid,
                    #     )
                    #     response.extend([b"200", b"SUCCESS", b"", b""])
                    #     _log.debug("AUTH response: {}".format(response))
                    #     sock.send_multipart(response)
                    # else:
                    if type(userid) == bytes:
                        userid = userid.decode("utf-8")
                    self.auth_service._update_auth_pending(domain, address, kind, credentials[0], userid)

                    try:
                        expire, delay = blocked[address]
                    except KeyError:
                        delay = random.random()
                    else:
                        if now >= expire:
                            delay = random.random()
                        else:
                            delay *= 2
                            if delay > 100:
                                delay = 100
                    expire = now + delay
                    bisect.bisect(wait_list, (expire, address, response))
                    blocked[address] = expire, delay
            while wait_list:
                expire, address, response = wait_list[0]
                if now < expire:
                    break
                wait_list.pop(0)
                response.extend([b"400", b"FAIL", b"", b""])
                sock.send_multipart(response)
                try:
                    if now >= blocked[address][0]:
                        blocked.pop(address)
                except KeyError:
                    pass
            timeout = (wait_list[0][0] - now) if wait_list else None

    def start(self):
        _log.debug(f"Starting {self.__class__.__name__}")
        if self.params is None:
            raise ValueError(f"Parameters not set before calling start on {self.__class__.__name__}")

        self._credential_manager: CredentialsManager = self.params.credential_manager
        self._auth_service = self.params.auth_service

        # These are for the server itself.
        publickey = None
        secretkey = None

        if self._auth_service is not None:
            if self._credential_manager is None:
                raise ValueError("Auth server and credential manager must both be set or neither be set.")

            self._start_zap()
            # if self.allow_any:
            #     _log.warning("insecure permissive authentication enabled")
            #if self._auth_service.greenlet is None:
            #    raise RuntimeError("Auth service must be started before starting message bus.")
            self._auth_service.read_auth_file()
            self._auth_service.start_watch_files()
            _log.debug("Spawing zap greenlet")
            self._zap_greenlet = gevent.spawn(self._zap_loop)
            #
            # # self._read_protected_topics_file()
            # self.core.spawn(watch_file, self.auth_file_path, self.read_auth_file)
            # self.core.spawn(
            #     watch_file,
            #     self._protected_topics_file_path,
            #     self._read_protected_topics_file,
            # )
            # if self.core.messagebus == "rmq":
            #     self.vip.peerlist.onadd.connect(self._check_topic_rules)
        else:
            if self._credential_manager is not None:
                raise ValueError("Credentail manager and auth must both be set or neither be set.")

        if self._credential_manager:
            _log.debug("Running zmq router")

            # Throws credential error if not found.
            server_creds = self._credential_manager.load("server")

            publickey = server_creds.credentials["public"]
            secretkey = server_creds.credentials["secret"]

        def zmq_router():

            Router(
                self.params.local_address,
                self.params.addresses,
                secretkey=secretkey,
                publickey=publickey,
                default_user_id="vip.service",
                #monitor=opts.monitor,
                # tracker=tracker,
                instance_name="my_instance",  # self.params.instance_name,
                # protected_topics=protected_topics,
                # external_address_file=external_address_file,
                # msgdebug=opts.msgdebug,
                # service_notifier=notifier,
            ).run()

        self._zmq_thread = gevent.spawn(zmq_router)
        # self._zmq_thread = threading.Thread(target=zmq_router, name="zmq_router", daemon=True)
        # self._zmq_thread.start()
        _log.debug("After thread start")

        # if not self._zmq_thread.is_alive():
        #     raise ValueError("Zmq Thread has Died!")

        _log.debug("Returning from start() messagebus.")
        # self.zmq_greenlet = gevent.spawn(zmq_router)

    def stop(self):
        _log.debug(f"Stopping {self.__class__.__name__}")

        if self._auth_service is not None:
            self._stop_zap()
