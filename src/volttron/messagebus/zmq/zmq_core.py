import json
import logging
import os
import sys
import urllib
from dataclasses import dataclass
from errno import ENOTSOCK, EAGAIN, ENOENT
from urllib.parse import parse_qs, urlsplit, urlunsplit

import gevent
import zmq
from zmq import ZMQError
from zmq.utils.monitor import recv_monitor_message

from volttron.client.vip.agent import Core
from volttron.messagebus.zmq.connection import ZmqConnectionContext
from volttron.messagebus.zmq.zmq_connection import ZMQConnection
from volttron.types import Credentials
from volttron.utils import ClientContext as cc

_log = logging.getLogger(__name__)
_log.setLevel(logging.DEBUG)


class ZmqCore(Core):
    """
    Concrete Core class for ZeroMQ message bus
    """

    def __init__(
        self,
        owner,
        address: str = None,
        credentials: Credentials = None,
        agent_uuid: str = None,
        reconnect_interval: int = None
    ):
        identity = credentials.identity
        super().__init__(
            owner=owner,
            address=address,
            identity=identity,
            reconnect_interval=reconnect_interval
        )

        server_credentials = cc.get_server_credentials()
        if credentials is None and server_credentials is not None or \
                credentials is not None and server_credentials is None:
            raise ValueError(f"If credentials are specified so should server_credentials {self.__class__.__name__}")

        self.secretkey = None
        self.serverkey = None
        self.publickey = None

        if credentials is None:
            _log.warning(f"Authentication mode off for {self.__class__.__name__}")
        else:
            creds = credentials.credentials
            server_creds = server_credentials.credentials
            self.publickey = creds["public"]
            self.secretkey = creds["secret"]
            self.serverkey = server_creds["public"]

        self._connection_context = ZmqConnectionContext(address=address,
                                                        identity=identity,
                                                        publickey=self.publickey,
                                                        secretkey=self.secretkey,
                                                        serverkey=self.serverkey,
                                                        reconnect_interval=reconnect_interval,
                                                        agent_uuid=agent_uuid)
        self.reconnect_interval = reconnect_interval
        self.address = address

        self.identity = identity
        self.agent_uuid = agent_uuid
        self.context = zmq.Context.instance()
        #self._set_keys_from_environment()

        _log.debug(f"AGENT RUNNING on ZMQ Core {self.identity}")
        _log.debug(
            f"keys: server: {self.serverkey} public: {self.publickey}, secret: {self.secretkey}")
        self.socket = None

    def get_connected(self):
        return super(ZmqCore, self).get_connected()

    def set_connected(self, value):
        super(ZmqCore, self).set_connected(value)

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
            self.publickey = json.loads(creds['server_credential'])['public']  #  os.environ.get("AGENT_PUBLICKEY")
            self.secretkey = json.loads(creds['server_credential'])['secret']  # os.environ.get("AGENT_SECRETKEY")
            _log.debug(
                f"after setting agent private and public key {self.publickey} {self.secretkey}")
        if self.publickey is None or self.secretkey is None:
            self.publickey, self.secretkey, _ = self._get_keys_from_addr()
        if self.publickey is None or self.secretkey is None:
            self.publickey, self.secretkey = self._get_keys_from_keystore()

    def _set_server_key(self):
        if self.serverkey is None:
            _log.debug(f"environ keys: {dict(os.environ).keys()}")
            _log.debug(f"server key from env {os.environ.get('VOLTTRON_SERVERKEY')}")
            creds = json.loads(os.environ.get('VOLTTRON_SERVER_CREDENTIAL'))

            self.serverkey = json.loads(creds['server_credential'])['public']  #  os.environ.get("VOLTTRON_SERVERKEY")

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
        # pre-setup
        # self.context.set(zmq.MAX_SOCKETS, 30690)
        self.connection = ZMQConnection(self._connection_context, self.context)
        # self.connection = ZMQConnection(self.address,
        #                                 self.identity,
        #                                 self.instance_name,
        #                                 context=self.context)
        self.connection.open_connection(zmq.DEALER)
        flags = dict(hwm=6000, reconnect_interval=self.reconnect_interval)
        self.connection.set_properties(flags)
        self.socket = self.connection.socket
        yield

        # pre-start
        state = type("HelloState", (), {"count": 0, "ident": None})

        hello_response_event = gevent.event.Event()
        connection_failed_check, hello, hello_response = self.create_event_handlers(
            state, hello_response_event, running_event)

        def close_socket(sender):
            gevent.sleep(2)
            try:
                if self.socket is not None:
                    self.socket.monitor(None, 0)
                    self.socket.close(1)
            finally:
                self.socket = None

        def monitor():
            # Call socket.monitor() directly rather than use
            # get_monitor_socket() so we can use green sockets with
            # regular contexts (get_monitor_socket() uses
            # self.context.socket()).
            addr = "inproc://monitor.v-%d" % (id(self.socket), )
            sock = None
            if self.socket is not None:
                try:
                    self.socket.monitor(addr)
                    sock = zmq.Socket(self.context, zmq.PAIR)

                    sock.connect(addr)
                    while True:
                        try:
                            message = recv_monitor_message(sock)
                            self.onsockevent.send(self, **message)
                            event = message["event"]

                            if event & zmq.EVENT_CONNECTED:
                                _log.debug(f"Event is {event} EVENT_CONNECTED")
                                hello()
                            elif event & zmq.EVENT_DISCONNECTED:
                                _log.debug(f"Event is {event} EVENT_DISCONNECTED")
                                self.connected = False
                            elif event & zmq.EVENT_CONNECT_RETRIED:
                                _log.debug(f"Event is {event} EVENT_CONNECT_RETRIED")
                                self._reconnect_attempt += 1
                                if self._reconnect_attempt == 50:
                                    self.connected = False
                                    sock.disable_monitor()
                                    self.stop()
                                    self.ondisconnected.send(self)
                            elif event & zmq.EVENT_MONITOR_STOPPED:
                                _log.debug(f"Event is {event} EVENT_MONITOR_STOPPED")
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
                        if self.socket is not None:
                            self.socket.monitor(None, 0)
                    except Exception as exc:
                        _log.debug("Error in closing the socket: {}".format(exc))

        self.onconnected.connect(hello_response)
        self.ondisconnected.connect(close_socket)

        _log.debug(f"Address is: {self.address}")
        # ipc = "ipc://%s$VOLTTRON_HOME/run/" % ("@" if sys.platform.startswith("linux") else "")
        # ipc = ipc.replace("$VOLTTRON_HOME", os.path.expanduser("~/.volttron"))
        # self.address = ipc
        if self.address[:4] in ["tcp:", "ipc:"]:
           self.spawn(monitor).join(0)
        self.connection.connect()
        if self.address.startswith("inproc:"):
            hello()

        def vip_loop():
            sock = self.socket
            while True:
                try:
                    # Message at this point in time will be a
                    # volttron.client.vip.socket.Message object that has attributes
                    # for the vip elements.  Note these are no longer bytes.
                    # see https://github.com/volttron/volttron/issues/2123
                    message = sock.recv_vip_object(copy=False)
                except ZMQError as exc:

                    if exc.errno == EAGAIN:
                        continue
                    elif exc.errno == ENOTSOCK:
                        self.socket = None
                        break
                    else:
                        raise
                subsystem = message.subsystem
                # _log.debug("Received new message {0}, {1}, {2}, {3}".format(
                #     subsystem, message.id, len(message.args), message.args[0]))

                # Handle hellos sent by CONNECTED event
                if (str(subsystem) == "hello" and message.id == state.ident
                        and len(message.args) > 3 and message.args[0] == "welcome"):
                    version, server, identity = message.args[1:4]
                    self.connected = True
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
                    handle(message)

        yield gevent.spawn(vip_loop)
        # pre-stop
        yield
        # pre-finish
        try:
            self.connection.disconnect()
            self.socket.monitor(None, 0)
            self.connection.close_connection(1)
        except AttributeError:
            pass
        except ZMQError as exc:
            if exc.errno != ENOENT:
                _log.exception("disconnect error")
        finally:
            self.socket = None
        yield


if __name__ == '__main__':
    a = object()
    core = ZmqCore()
    zmq_con = ZmqConnectionContext()
    assert zmq_con