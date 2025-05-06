import logging
import re

import gevent
import gevent.core
import zmq.green as zmq

# Import callable time function.
from time import time


from volttron.types.auth.auth_service import Authenticator, CredentialsStore, Credentials
from volttron.types.auth.authz_types import Identity
from volttron.server.server_options import ServerOptions
from volttron.decorators import service


from .credentials_creator import encode_key

from volttron.utils import get_logger

_log = get_logger()

_dump_re = re.compile(r"([,\\])")
_load_re = re.compile(r"\\(.)|,")


def isregex(obj):
    return len(obj) > 1 and obj[0] == obj[-1] == "/"


def dump_user(*args) -> str:
    return ",".join([_dump_re.sub(r"\\\1", arg) for arg in args])


def load_user(string):
    def sub(match):
        return match.group(1) or "\x00"

    return _load_re.sub(sub, string).split("\x00")


@service
class ZapAuthenticator(Authenticator):
    def __init__(self, *, options: ServerOptions, credentials_store: CredentialsStore):

        self._credentials_store = credentials_store
        self._options = options
        self.zap_socket = zmq.Socket(zmq.Context.instance(), zmq.ROUTER)
        self.zap_socket.bind("inproc://zeromq.zap.01")
        self._authenticated: set[Identity] = set()

        self._zap_greenlet = gevent.spawn(self.zap_loop)

        # TODO: How do we want to do the permissive thing here.
        # if self.allow_any:
        #     _log.warning("insecure permissive authentication enabled")
        # self.read_auth_file()
        # self._read_protected_topics_file()
        # self.core.spawn(watch_file, self.auth_file_path, self.read_auth_file)
        # self.core.spawn(
        #     watch_file,
        #     self._protected_topics_file_path,
        #     self._read_protected_topics_file,
        # )

    def is_authenticated(self, *, identity: Identity) -> bool:
        return identity in self._authenticated

    # def authenticate(self, *, credentials: Credentials) -> bool:
    #     if self._options.auth_enabled:
    #         if not self._credentials_store.has_identity(credentials.identity):
    #             # Might be other stuff here to work with.
    #             return False
    #     # creds = self._credentials_store.retrieve_credentials(credentials.identity)
    #     return True

    def zap_loop(self):
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
        blocked = {}
        wait_list = []
        timeout = None
        _log.debug("Starting Zap Loop")
        # TODO topic permissions?
        # if self.core.messagebus == "rmq":
        #     # Check the topic permissions of all the connected agents
        #     self._check_rmq_topic_permissions()
        # else:
        #     self._send_protected_update_to_pubsub(self._protected_topics)

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
                user = self.zap_authenticate(domain, address, credentials[0])
                _log.info(f"AUTH: After authenticate user id: {user}, {userid}")
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
                    # If in setup mode, add/update auth entry
                    if self._setup_mode:
                        self._update_auth_entry(domain, address, kind, credentials[0], userid)
                        _log.info(
                            "new authentication entry added in setup mode: domain=%r, address=%r, "
                            "mechanism=%r, credentials=%r, user_id=%r",
                            domain,
                            address,
                            kind,
                            credentials[:1],
                            userid,
                        )
                        response.extend([b"200", b"SUCCESS", b"", b""])
                        _log.debug("AUTH response: {}".format(response))
                        sock.send_multipart(response)
                    else:
                        if type(userid) == bytes:
                            userid = userid.decode("utf-8")
                        self._update_auth_pending(domain, address, kind, credentials[0], userid)

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

    def zap_authenticate(self, domain: str, address: str, credentials: str) -> str | None:
        """
        Authenticate the zap connection, retrieving the user id from the credential store.

        If successful the return value will be the identity of the connection stored in the
        credential store.

        If NOT successful the return value will be None

        :param domain: filters the config store to a specific domain.
        :type domain: str
        :param address: filters the connection based upon address
        :type address: str
        :param credentials: A zap publickey that should be used to authenticate against.
        :type credentials: str
        :return: Either the identity of a user or None depending on whether authenticated.
        :rtype: str | None
        """

        _log.debug(f"AUTH: domain={domain}, address={address}, credentials={credentials}")

        matched_credential = self._credentials_store.retrieve_credentials(publickey=credentials,
                                                                          domain=domain,
                                                                          address=address)

        if matched_credential:
            return matched_credential.identity

        # for entry in self.auth_entries:
        #     if entry.match(domain, address, mechanism, credentials):
        #         return entry.user_id or dump_user(domain, address, mechanism, *credentials[:1])
        # if mechanism == "NULL" and address.startswith("localhost:"):
        #     parts = address.split(":")[1:]
        #     if len(parts) > 2:
        #         pid = int(parts[2])
        #         agent_uuid = self.aip.agent_uuid_from_pid(pid)
        #         if agent_uuid:
        #             return dump_user(domain, address, "AGENT", agent_uuid)
        #     uid = int(parts[0])
        #     if uid == os.getuid():
        #         return dump_user(domain, address, mechanism, *credentials[:1])
        # TODO: Do we have an allow_any?
        # if self.allow_any:
        #     return dump_user(domain, address, mechanism, *credentials[:1])
