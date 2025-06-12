import pytest
from tests.fixtures.mock_classes import MockZmqSocket, MockZmqPoller, MockZmqContext


@pytest.fixture  
def test_frames():
    return [b"sender", b"recipient", b"VIP1", b"token", b"msg_id", b"hello"]

@pytest.fixture
def mock_zmq_context():
    """Mock ZMQ context"""
    context = MockZmqContext()
    yield context
    context.term()

@pytest.fixture
def mock_zmq_socket():
    """Mock ZMQ socket"""
    return MockZmqSocket()

@pytest.fixture  
def mock_zmq_poller():
    """Mock ZMQ poller"""
    return MockZmqPoller()

@pytest.fixture
def router_probe_frames():
    """Router probe frames"""
    return [b"probe_sender", b""]

@pytest.fixture
def insufficient_frames():
    """Frames with insufficient data"""
    return [b"sender", b"recipient"]

@pytest.fixture
def serialized_test_frames(test_frames):
    """Serialized test frames"""
    from volttron.messagebus.zmq.serialize_frames import serialize_frames
    return serialize_frames(test_frames)