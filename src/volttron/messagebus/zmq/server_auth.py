import bisect
import logging
import random
import uuid

import gevent
from volttron.platform.auth import (BaseServerAuthentication, BaseServerAuthorization)
from volttron.services.auth.auth_service import AuthEntry
from volttron.utils import encode_key

_log = logging.getLogger(__name__)


class ZMQServerAuthentication(BaseServerAuthentication):
    """
    Implementation of the Zap Loop used by AuthService
    for handling ZMQ Authentication on the VOLTTRON Server Instance
    """

    def __init__(self, auth_service) -> None:
        super().__init__(auth_service=auth_service)
        self.zap_socket = None
        self._zap_greenlet = None
        self.authorization = ZMQAuthorization(self.auth_service)

    def setup_authentication(self):
        self.zap_socket = zmq.Socket(zmq.Context.instance(), zmq.ROUTER)
        self.zap_socket.bind("inproc://zeromq.zap.01")

    def authenticate(self, domain, address, mechanism, credentials):
        for entry in self.auth_service.auth_entries:
            # _log.info(f"Auth entry: {entry}")
            # _log.info(f"Incoming auth: \n"
            #           f"domain: {domain}\n"
            #           f"address: {address}\n"
            #           f"mechanism: {mechanism}")
            if entry.match(domain, address, mechanism, credentials):
                return entry.user_id or dump_user(domain, address, mechanism, *credentials[:1])
        if mechanism == "NULL" and address.startswith("localhost:"):
            parts = address.split(":")[1:]
            if len(parts) > 2:
                pid = int(parts[2])
                agent_uuid = self.auth_service.aip.agent_uuid_from_pid(pid)
                if agent_uuid:
                    return dump_user(domain, address, "AGENT", agent_uuid)
            uid = int(parts[0])
            if uid == os.getuid():
                return dump_user(domain, address, mechanism, *credentials[:1])
        if self.auth_service.allow_any:
            return dump_user(domain, address, mechanism, *credentials[:1])

    def handle_authentication(self, protected_topics):
        """
        The zap loop is the starting of the authentication process for
        the VOLTTRON zmq message bus.  It talks directly with the low
        level socket so all responses must be byte like objects, in
        this case we are going to send zmq frames across the wire.

        :param sender:
        :param kwargs:
        :return:
        """
        self.auth_service._is_connected = True
        self._zap_greenlet = gevent.getcurrent()
        sock = self.zap_socket
        blocked = {}
        wait_list = []
        timeout = None

        self.authorization.update_protected_topics(protected_topics)
        while True:
            events = sock.poll(timeout)
            now = gevent.time.time()
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
                user = self.authenticate(domain, address, kind, credentials)
                _log.info("AUTH: After authenticate user id: %r, %r", user, userid)
                if user:
                    _log.info(
                        "authentication success: userid=%r domain=%r, "
                        "address=%r, "
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
                        "authentication failure: userid=%r, domain=%r, "
                        "address=%r, "
                        "mechanism=%r, credentials=%r",
                        userid,
                        domain,
                        address,
                        kind,
                        credentials,
                    )
                    # If in setup mode, add/update auth entry
                    if self.auth_service._setup_mode:
                        self.authorization._update_auth_entry(domain, address, kind, credentials[0], userid)
                        _log.info(
                            "new authentication entry added in setup mode: "
                            "domain=%r, address=%r, "
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

    def stop_authentication(self):
        if self._zap_greenlet is not None:
            self._zap_greenlet.kill()

    def unbind_authentication(self):
        if self.zap_socket is not None:
            self.zap_socket.unbind("inproc://zeromq.zap.01")

    def _update_auth_pending(self, domain, address, mechanism, credential, user_id):
        """Handles incoming pending auth entries."""
        for entry in self.auth_service._auth_denied:
            # Check if failure entry has been denied. If so, increment the
            # failure's denied count
            if ((entry["domain"] == domain) and (entry["address"] == address) and (entry["mechanism"] == mechanism)
                    and (entry["credentials"] == credential)):
                entry["retries"] += 1
                return

        for entry in self.auth_service._auth_pending:
            # Check if failure entry exists. If so, increment the failure count
            if ((entry["domain"] == domain) and (entry["address"] == address) and (entry["mechanism"] == mechanism)
                    and (entry["credentials"] == credential)):
                entry["retries"] += 1
                return
        # Add a new failure entry
        fields = {
            "domain": domain,
            "address": address,
            "mechanism": mechanism,
            "credentials": credential,
            "user_id": user_id,
            "retries": 1,
        }
        self.auth_service._auth_pending.append(dict(fields))
        return


class ZMQAuthorization(BaseServerAuthorization):

    def __init__(self, auth_service):
        super().__init__(auth_service=auth_service)

    def create_authenticated_address(self):
        pass

    def approve_authorization(self, user_id):
        index = 0
        matched_index = -1
        for pending in self.auth_service._auth_pending:
            if user_id == pending["user_id"]:
                self._update_auth_entry(
                    pending["domain"],
                    pending["address"],
                    pending["mechanism"],
                    pending["credentials"],
                    pending["user_id"],
                )
                matched_index = index
                break
            index = index + 1
        if matched_index >= 0:
            del self.auth_service._auth_pending[matched_index]

        for pending in self.auth_service._auth_denied:
            if user_id == pending["user_id"]:
                self.auth_service.auth_file.approve_deny_credential(user_id, is_approved=True)

    def deny_authorization(self, user_id):
        index = 0
        matched_index = -1
        for pending in self.auth_service._auth_pending:
            if user_id == pending["user_id"]:
                self._update_auth_entry(
                    pending["domain"],
                    pending["address"],
                    pending["mechanism"],
                    pending["credentials"],
                    pending["user_id"],
                    is_allow=False,
                )
                matched_index = index
                break
            index = index + 1
        if matched_index >= 0:
            del self.auth_service._auth_pending[matched_index]

        for pending in self.auth_service._auth_approved:
            if user_id == pending["user_id"]:
                self.auth_service.auth_file.approve_deny_credential(user_id, is_approved=False)

    def delete_authorization(self, user_id):
        index = 0
        matched_index = -1
        for pending in self.auth_service._auth_pending:
            if user_id == pending["user_id"]:
                self._update_auth_entry(
                    pending["domain"],
                    pending["address"],
                    pending["mechanism"],
                    pending["credentials"],
                    pending["user_id"],
                )
                matched_index = index
                val_err = None
                break
            index = index + 1
        if matched_index >= 0:
            del self.auth_service._auth_pending[matched_index]

        index = 0
        matched_index = -1
        for pending in self.auth_service._auth_pending:
            if user_id == pending["user_id"]:
                matched_index = index
                val_err = None
                break
            index = index + 1
        if matched_index >= 0:
            del self.auth_service._auth_pending[matched_index]

        for pending in self.auth_service._auth_approved:
            if user_id == pending["user_id"]:
                self._remove_auth_entry(pending["credentials"])
                val_err = None

        for pending in self.auth_service._auth_denied:
            if user_id == pending["user_id"]:
                self._remove_auth_entry(pending["credentials"], is_allow=False)
                val_err = None

        # If the user_id supplied was not for a ZMQ server_credential, and the
        # pending_csr check failed,
        # output the ValueError message to the error log.
        if val_err:
            _log.error(f"{val_err}")

    def update_user_capabilites(self, user_to_caps):
        # Send auth update message to router
        json_msg = jsonapi.dumpb(dict(capabilities=user_to_caps))
        frames = [zmq.Frame(b"auth_update"), zmq.Frame(json_msg)]
        # <recipient, subsystem, args, msg_id, flags>
        self.auth_service.core.socket.send_vip(b"", b"pubsub", frames, copy=False)

    def load_protected_topics(self, protected_topics_data):
        protected_topics = super().load_protected_topics(protected_topics_data)
        self.update_protected_topics(protected_topics)
        return protected_topics

    def update_protected_topics(self, protected_topics):
        from volttron.platform.vip.agent.errors import VIPError
        protected_topics_msg = jsonapi.dumpb(protected_topics)

        frames = [
            zmq.Frame(b"protected_update"),
            zmq.Frame(protected_topics_msg),
        ]
        if self.auth_service._is_connected:
            try:
                # <recipient, subsystem, args, msg_id, flags>
                self.auth_service.core.socket.send_vip(b"", b"pubsub", frames, copy=False)
            except VIPError as ex:
                _log.error(
                    "Error in sending protected topics update to clear "
                    "PubSub: %s",
                    ex,
                )

    def _update_auth_entry(self, domain, address, mechanism, credential, user_id, is_allow=True):
        """Adds a pending auth entry to AuthFile."""
        # Make a new entry
        fields = {
            "domain": domain,
            "address": address,
            "mechanism": mechanism,
            "credentials": credential,
            "user_id": user_id,
            "groups": "",
            "roles": "",
            "capabilities": "",
            "rpc_method_authorizations": {},
            "comments": "Auth entry added in setup mode",
        }
        new_entry = AuthEntry(**fields)

        try:
            self.auth_service.auth_file.add(new_entry, overwrite=False, is_allow=is_allow)
        except AuthException as err:
            _log.error("ERROR: %s\n", str(err))

    def _remove_auth_entry(self, credential, is_allow=True):
        try:
            self.auth_service.auth_file.remove_by_credentials(credential, is_allow=is_allow)
        except AuthException as err:
            _log.error("ERROR: %s\n", str(err))

    def get_authorization(self, user_id):
        for auth_entry in self.auth_service._auth_pending:
            if user_id == auth_entry.user_id:
                return str(auth_entry.credentials)
        for auth_entry in self.auth_service._auth_approved:
            if user_id == auth_entry.user_id:
                return str(auth_entry.credentials)
        for auth_entry in self.auth_service._auth_denied:
            if user_id == auth_entry.user_id:
                return str(auth_entry.credentials)
        return ""

    def get_authorization_status(self, user_id):
        for auth_entry in self.auth_service._auth_pending:
            if user_id == auth_entry.user_id:
                return "PENDING"
        for auth_entry in self.auth_service._auth_approved:
            if user_id == auth_entry.user_id:
                return "APPROVED"
        for auth_entry in self.auth_service._auth_denied:
            if user_id == auth_entry.user_id:
                return "DENIED"
        return "UNKOWN"

    def get_pending_authorizations(self):
        return list(self.auth_service._auth_pending)

    def get_approved_authorizations(self):
        return list(self.auth_service._auth_approved)

    def get_denied_authorizations(self):
        return list(self.auth_service._auth_denied)
