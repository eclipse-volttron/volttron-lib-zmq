"""The router must file, find, sync and remove a peer's subscriptions under one and the same bus key: the bus the
client names. Filing subscribes under a literal 'pubsub' while synchronize, unsubscribe and list used the client's bus
meant a synchronize (sent by a client for any message it had no handler for) dropped the peer from every working
subscription and re-added them where distribution never looked."""
from unittest.mock import MagicMock

import pytest

from volttron.messagebus.zmq.routing.pubsub_service import PubSubService

PEER, OTHER = "agent.a", "agent.b"


@pytest.fixture
def service():
    svc = PubSubService(socket=MagicMock(), auth_service=None, routing_service=None)
    svc.sent = []
    svc._send = lambda frames, publisher: svc.sent.append((frames[0], frames[7])) or []
    return svc


def subscribe(svc, peer, prefix, bus=""):
    return svc._peer_subscribe([peer, "", "VIP1", "", "1", "pubsub", "subscribe",
                                {"prefix": prefix, "bus": bus, "all_platforms": False}])


def publish(svc, publisher, topic, bus=""):
    svc.sent.clear()
    svc._distribute_internal([publisher, "", "VIP1", "", "2", "pubsub", "publish", topic,
                              {"bus": bus, "headers": {}, "message": 1, "sender": publisher}])
    return [peer for peer, _ in svc.sent]


def test_subscription_is_filed_under_the_clients_bus_and_delivered(service):
    assert subscribe(service, PEER, "devices/x")
    assert service._peer_subscriptions["internal"][""]["devices/x"] == {PEER}
    assert publish(service, OTHER, "devices/x/all") == [PEER]


def test_synchronize_keeps_a_working_subscription(service):
    subscribe(service, PEER, "devices/x")
    service._peer_sync([PEER, "", "VIP1", "", "3", "pubsub", "synchronize", "connected",
                        {"subscriptions": {"internal": {"": ["devices/x"]}}}])
    assert publish(service, OTHER, "devices/x/all") == [PEER]


def test_synchronize_drops_what_the_client_no_longer_has(service):
    subscribe(service, PEER, "devices/x")
    subscribe(service, PEER, "devices/y")
    service._peer_sync([PEER, "", "VIP1", "", "3", "pubsub", "synchronize", "connected",
                        {"subscriptions": {"internal": {"": ["devices/y"]}}}])
    assert publish(service, OTHER, "devices/x/all") == []
    assert publish(service, OTHER, "devices/y/all") == [PEER]


def test_unsubscribe_removes_the_subscription(service):
    subscribe(service, PEER, "devices/x")
    assert service._peer_unsubscribe([PEER, "", "VIP1", "", "4", "pubsub", "unsubscribe",
                                      {"internal": {"prefix": ["devices/x"], "bus": ""}}])
    assert publish(service, OTHER, "devices/x/all") == []


def test_buses_stay_apart(service):
    subscribe(service, PEER, "devices/x", bus="")
    subscribe(service, OTHER, "devices/x", bus="other")
    assert publish(service, "p", "devices/x/all", bus="") == [PEER]
    assert publish(service, "p", "devices/x/all", bus="other") == [OTHER]
