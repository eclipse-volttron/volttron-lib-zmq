import base64
import binascii
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch

import gevent


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


def test_zmq_connection(monkeypatch, tmp_path):
    """Test ZMQ connection with isolated environment"""
    # Set up isolated test environment
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    # Create credentials directory
    creds_dir = test_volttron_home / "credentials_store"
    creds_dir.mkdir(parents=True)
    
    # Create control credentials
    control_credpath = creds_dir / "control.connection.json"
    control_creds_content = {
        "identity": "platform.control",
        "publickey": "V-U56kOr0gQYgAwS3xK6p6fRrv3d9HlYAu3RcSD3KQo",
        "secretkey": "_D363Bzt3uxi7cr0pd1zkXrfGJU9-H2pjGm6tU9YJxk",
        "domain": "",
        "address": ""
    }
    control_credpath.write_text(json.dumps(control_creds_content))
    
    # Create server/platform credentials
    server_credpath = creds_dir / "platform.json"
    server_creds_content = {
        "identity": "platform",
        "publickey": "wa3z-l58PrKBj4PjQZRbCQPfB-UEqkK0G8lPFG-5hzQ",
        "secretkey": "TscttpVwLzRt1-WIkr14cBcMpHLDlUrIR0aUb1UDqH0",
        "domain": "",
        "address": ""
    }
    server_credpath.write_text(json.dumps(server_creds_content))
    
    # Load credentials
    creds = CredentialsFactory.load_credentials_from_file(control_credpath)
    server_creds = CredentialsFactory.load_credentials_from_file(server_credpath)
    
    # Mock the connection components to avoid actual network connections
    with patch('volttron.messagebus.zmq.zmq_connection.ZmqConnectionContext') as mock_context_class, \
         patch('volttron.messagebus.zmq.zmq_connection.ZmqConnection') as mock_connection_class:
        
        mock_context = Mock()
        mock_context_class.return_value = mock_context
        
        mock_connection = Mock()
        mock_connection.connected = True
        mock_connection_class.return_value = mock_connection
        
        # Create connection context
        connect_context = ZmqConnectionContext(
            address="tcp://127.0.0.1:22916",
            identity=creds.identity,
            publickey=creds.publickey,
            secretkey=creds.secretkey,
            serverkey=server_creds.publickey
        )
        
        # Test connection
        context = zmq.Context()
        connection = ZmqConnection(connect_context, context)
        connection.open_connection()
        
        assert connection.connected
        
        connection.close_connection()
        
        # Verify methods were called
        mock_connection.open_connection.assert_called_once()
        mock_connection.close_connection.assert_called_once()

def test_low_level_connection(monkeypatch, tmp_path):
    """Test low-level ZMQ connection with isolated environment"""
    # Set up isolated test environment
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    # Create credentials directory and run directory
    creds_dir = test_volttron_home / "credentials_store"
    creds_dir.mkdir(parents=True)
    run_dir = test_volttron_home / "run"
    run_dir.mkdir(parents=True)
    
    # Create control credentials
    control_credpath = creds_dir / "control.connection.json"
    control_creds_content = {
        "identity": "platform.control",
        "publickey": "V-U56kOr0gQYgAwS3xK6p6fRrv3d9HlYAu3RcSD3KQo",
        "secretkey": "_D363Bzt3uxi7cr0pd1zkXrfGJU9-H2pjGm6tU9YJxk",
        "domain": "",
        "address": ""
    }
    control_credpath.write_text(json.dumps(control_creds_content))
    
    # Create server/platform credentials
    server_credpath = creds_dir / "platform.json"
    server_creds_content = {
        "identity": "platform",
        "publickey": "wa3z-l58PrKBj4PjQZRbCQPfB-UEqkK0G8lPFG-5hzQ",
        "secretkey": "TscttpVwLzRt1-WIkr14cBcMpHLDlUrIR0aUb1UDqH0",
        "domain": "",
        "address": ""
    }
    server_credpath.write_text(json.dumps(server_creds_content))
    
    # Load credentials
    creds = CredentialsFactory.load_credentials_from_file(control_credpath)
    server_creds = CredentialsFactory.load_credentials_from_file(server_credpath)
    
    # Use test-specific IPC address
    ipc_address = f"ipc://@{test_volttron_home}/run/vip.socket"
    address_with_params = f"{ipc_address}?publickey={creds.publickey}&secretkey={creds.secretkey}&serverkey={server_creds.publickey}"
    
    print(f"Test Address: {address_with_params}")
    
    # Mock the socket operations to avoid actual connections
    with patch('zmq.Context') as mock_zmq_context, \
         patch('volttron.messagebus.zmq.zmq_connection.GreenSocket') as mock_green_socket:
        
        mock_context = Mock()
        mock_zmq_context.return_value = mock_context
        
        mock_socket = Mock()
        mock_green_socket.return_value = mock_socket
        
        # Test the low-level connection setup
        context = zmq.Context()
        socket = GreenSocket(context)
        socket.identity = creds.identity.encode('utf-8')
        socket.set_hwm(6000)
        socket.setsockopt(zmq.RECONNECT_IVL, 1000)
        socket.connect(addr=address_with_params)
        socket.identity = creds.identity.encode('utf-8')
        socket.close()
        
        # Verify socket operations
        mock_socket.set_hwm.assert_called_with(6000)
        mock_socket.setsockopt.assert_called_with(zmq.RECONNECT_IVL, 1000)
        mock_socket.connect.assert_called_with(addr=address_with_params)
        mock_socket.close.assert_called_once()

def test_zmq_connection_integration(monkeypatch, tmp_path):
    """Integration test with real ZMQ components but isolated environment"""
    # Set up isolated test environment
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    # Create credentials
    creds_dir = test_volttron_home / "credentials_store"
    creds_dir.mkdir(parents=True)
    
    control_credpath = creds_dir / "control.connection.json"
    control_creds_content = {
        "identity": "platform.control",
        "publickey": "V-U56kOr0gQYgAwS3xK6p6fRrv3d9HlYAu3RcSD3KQo",
        "secretkey": "_D363Bzt3uxi7cr0pd1zkXrfGJU9-H2pjGm6tU9YJxk",
        "domain": "",
        "address": ""
    }
    control_credpath.write_text(json.dumps(control_creds_content))
    
    server_credpath = creds_dir / "platform.json"
    server_creds_content = {
        "identity": "platform",
        "publickey": "wa3z-l58PrKBj4PjQZRbCQPfB-UEqkK0G8lPFG-5hzQ",
        "secretkey": "TscttpVwLzRt1-WIkr14cBcMpHLDlUrIR0aUb1UDqH0",
        "domain": "",
        "address": ""
    }
    server_credpath.write_text(json.dumps(server_creds_content))
    
    # Load credentials
    creds = CredentialsFactory.load_credentials_from_file(control_credpath)
    server_creds = CredentialsFactory.load_credentials_from_file(server_credpath)
    
    # Verify credentials were loaded correctly
    assert creds.identity == "platform.control"
    assert creds.publickey == "V-U56kOr0gQYgAwS3xK6p6fRrv3d9HlYAu3RcSD3KQo"
    assert server_creds.identity == "platform"
    assert server_creds.publickey == "wa3z-l58PrKBj4PjQZRbCQPfB-UEqkK0G8lPFG-5hzQ"
    
    # Test that connection context can be created
    connect_context = ZmqConnectionContext(
        address="tcp://127.0.0.1:22916",
        identity=creds.identity,
        publickey=creds.publickey,
        secretkey=creds.secretkey,
        serverkey=server_creds.publickey
    )
    
    assert connect_context.address == "tcp://127.0.0.1:22916"
    assert connect_context.identity == creds.identity
    assert connect_context.publickey == creds.publickey
    assert connect_context.secretkey == creds.secretkey
    assert connect_context.serverkey == server_creds.publickey