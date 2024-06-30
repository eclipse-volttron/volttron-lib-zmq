import base64
import os
from pathlib import Path
import gevent
os.environ['GEVENT_SUPPORT'] = 'True'


from zmq.utils import z85
import zmq.green as zmq
from zmq.green import Socket as GreenSocket

from volttron.types.auth import CredentialsFactory
from volttron.client.known_identities import CONTROL_CONNECTION, PLATFORM
#from volttron.messagebus.zmq import ZmqConnection, ZmqConnectionContext
from volttron.messagebus.zmq.zmq_connection import ZmqConnection, ZmqConnectionContext

os.environ['VOLTTRON_HOME'] = '/home/os2204/.volttron_redo'

def decode_key(key):
    '''Parse and return a Z85 encoded key from other encodings.'''
    if isinstance(key, str):
        key = key.encode("ASCII")
    length = len(key)
    if length == 40:
        return key
    elif length == 43:
        return z85.encode(base64.urlsafe_b64decode(key + '='.encode("ASCII")))
    elif length == 44:
        return z85.encode(base64.urlsafe_b64decode(key))
    elif length == 54:
        return base64.urlsafe_b64decode(key + '=='.encode("ASCII"))
    elif length == 56:
        return base64.urlsafe_b64decode(key)
    elif length == 64:
        return z85.encode(binascii.unhexlify(key))
    elif length == 80:
        return binascii.unhexlify(key)
    raise ValueError('unknown key encoding')


def test_zmq_connection():
    credpath = Path(os.environ['VOLTTRON_HOME']) / "credentials_store/control.connection.json"
    servercredpath = Path(os.environ['VOLTTRON_HOME']) / "credentials_store/platform.json"
    creds = CredentialsFactory.create_from_file(CONTROL_CONNECTION, credpath)
    server_creds = CredentialsFactory.create_from_file(PLATFORM, credpath)
    connect_context = ZmqConnectionContext(address="tcp://127.0.0.1:22916",
                                           identity=creds.identity,
                                           publickey=creds.publickey,
                                           secretkey=creds.secretkey,
                                           serverkey=server_creds.publickey)
    context = zmq.Context()
    connection = ZmqConnection(connect_context, context)
    connection.open_connection()
    assert connection.connected
    connection.close_connection()




def test_low_level_connection():
    credpath = Path(os.environ['VOLTTRON_HOME']) / "credentials_store/control.connection.json"
    servercredpath = Path(os.environ['VOLTTRON_HOME']) / "credentials_store/platform.json"
    creds = CredentialsFactory.create_from_file(CONTROL_CONNECTION, credpath)
    server_creds = CredentialsFactory.create_from_file(PLATFORM, credpath)
#ipc://@/home/ubuntu/.volttron/run/vip.socket?publickey=8TuMEIL5BfFtwclq1ZQT6OND0fgBbBqRWVDgCLbYiSI&secretkey=TscttpVwLzRt1-WIkr14cBcMpHLDlUrIR0aUb1UDqH0&serverkey=wa3z-l58PrKBj4PjQZRbCQPfB-UEqkK0G8lPFG-5hzQ
    ipc_address = "ipc://@/home/os2204/.volttron_redo/run/vip.socket"
    address_with_params = f"{ipc_address}?publickey={creds.publickey}&secretkey={creds.secretkey}&serverkey={server_creds.publickey}"
    print("Address: ", address_with_params)
    #address_with_params = f"tcp://127.0.0.1:22916?publickey={creds.publickey}&secretkey={creds.secretkey}&serverkey={server_creds.publickey}"
    context = zmq.Context()
    socket = GreenSocket(context)
    socket.identity = creds.identity.encode('utf-8')
    socket.set_hwm(6000)
    socket.setsockopt(zmq.RECONNECT_IVL, 1000)
    socket.connect(addr=address_with_params)
    socket.identity = creds.identity.encode('utf-8')

    socket.close()
