import logging
from multiprocessing import Process

import gevent
import pytest

from volttron.messagebus.zmq import ZmqCore, ZmqMessageBusParams, ZmqCredentialGenerator, ZmqMessageBus
from volttron.services.auth import AuthService
from volttron.types import Credentials
from volttron.types.errors import NotFoundError, MessageBusConnectionError

logging.basicConfig(level=logging.DEBUG)

@pytest.fixture
def server_creds():
    gen = ZmqCredentialGenerator()

    yield gen.generate("platform")


@pytest.fixture
def agent_creds():
    gen = ZmqCredentialGenerator()

    yield gen.generate("agent")


class InstanceWrapper:
    def __init__(self, message_bus_params: ZmqMessageBusParams):
        self.message_bus_params = message_bus_params
        self.process = None

    def start_instance(self):
        self.process = Process()



class Connector(object):
    def __init__(self, **kwargs):
        self.core = ZmqCore(owner=self, **kwargs)


def test_zmq_non_auth():
    params: ZmqMessageBusParams = ZmqMessageBus.get_default_parameters()
    mb = ZmqMessageBus()
    mb.set_parameters(params)
    mb.start()

    connector = Connector(address=params.local_address, identity="identity_test")
    event = gevent.event.Event()
    task = gevent.spawn(connector.core.run, event)
    with gevent.Timeout(1, MessageBusConnectionError) as timeout:
        event.wait()

    #
    # assert mb._zap_socket is None
    # # assert mb._zmq_thread is not None
    # assert mb.auth_service is None


    mb.stop()


def test_zmq_messagebus_validations():
    params = ZmqMessageBus.get_default_parameters()
    assert params
    with pytest.raises(NotFoundError) as not_found:
        assert params.get_parameter("unknown_param")

    with pytest.raises(ValueError) as missing_value:
        mb = ZmqMessageBus()
        mb.start()
        mb.stop()


def test_cred_generator():
    gen = ZmqCredentialGenerator()
    cred = gen.generate("id1")
    assert isinstance(cred, Credentials)
    assert cred.identity == "id1"
    assert cred.type == 'CURVE'
    assert cred.credentials["public"]
    assert cred.credentials["secret"]

    cred2 = gen.generate("id1")
    assert cred != cred2
