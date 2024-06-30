import os
from pathlib import Path
from volttron.client.vip.agent import Agent
from volttron.types.auth import Credentials, PKICredentials, CredentialsFactory
from volttron.client.known_identities import CONTROL_CONNECTION
import gevent

os.environ['VOLTTRON_HOME'] = '/home/os2204/.volttron_redo'

# from volttron.messagebus.zmq import ZmqConnectionParams


# def test_build_parameters():
#     ZmqConnectionParams()


def test_cred_factory():
    credpath = Path(os.environ['VOLTTRON_HOME']) / "credentials_store/control.connection.json"
    creds = CredentialsFactory.create_from_file(CONTROL_CONNECTION, credpath)

    assert creds.identity == CONTROL_CONNECTION
    assert creds.publickey
    assert creds.secretkey
    assert isinstance(creds, PKICredentials)


def test_create_agent():
    credpath = Path(os.environ['VOLTTRON_HOME']) / "credentials_store/control.connection.json"
    creds = CredentialsFactory.create_from_file(CONTROL_CONNECTION, credpath)
    agent = Agent(credentials=creds, address="tcp://127.0.0.1:22916")

    assert agent
    timeout = 5

    event = gevent.event.Event()
    gevent.spawn(agent.core.run, event)
    with gevent.Timeout(timeout):
        event.wait()

