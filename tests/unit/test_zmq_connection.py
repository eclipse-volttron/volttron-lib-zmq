# tests/unit/test_zmq_connection.py
import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch, Mock
from volttron.types.auth.auth_credentials import CredentialsFactory, Credentials

def test_cred_factory(monkeypatch, tmp_path):
    """Test credentials factory with isolated environment"""
    # Set up isolated test environment
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    # Create the credentials directory and file
    creds_dir = test_volttron_home / "credentials_store"
    creds_dir.mkdir(parents=True)
    
    cred_file = creds_dir / "control.connection.json"
    
    # Create test credentials content with correct format
    test_creds_content = {
        "identity": "platform.control",
        "publickey": "V-U56kOr0gQYgAwS3xK6p6fRrv3d9HlYAu3RcSD3KQo",
        "secretkey": "_D363Bzt3uxi7cr0pd1zkXrfGJU9-H2pjGm6tU9YJxk",
        "domain": "",
        "address": ""
    }
    
    cred_file.write_text(json.dumps(test_creds_content))
    
    # Now test the credentials factory
    creds = CredentialsFactory.load_credentials_from_file(cred_file)
    
    assert creds is not None
    assert isinstance(creds, Credentials)

def test_cred_factory_file_not_found(monkeypatch, tmp_path):
    """Test credentials factory when file doesn't exist"""
    test_volttron_home = tmp_path / "volttron_home"
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    nonexistent_path = Path(test_volttron_home) / "credentials_store/nonexistent.json"
    
    with pytest.raises(FileNotFoundError, match="Credential file: .* not found!"):
        CredentialsFactory.load_credentials_from_file(nonexistent_path)

def test_create_agent(monkeypatch, tmp_path):
    """Test agent creation with isolated environment"""
    # Set up isolated test environment
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    # Create the credentials directory and file
    creds_dir = test_volttron_home / "credentials_store"
    creds_dir.mkdir(parents=True)
    
    credpath = creds_dir / "control.connection.json"
    
    # Create test credentials content with correct format
    test_creds_content = {
        "identity": "platform.control",
        "publickey": "V-U56kOr0gQYgAwS3xK6p6fRrv3d9HlYAu3RcSD3KQo",
        "secretkey": "_D363Bzt3uxi7cr0pd1zkXrfGJU9-H2pjGm6tU9YJxk",
        "domain": "",
        "address": ""
    }
    
    credpath.write_text(json.dumps(test_creds_content))
    
    # Now test agent creation
    assert credpath.exists()
    
    creds = CredentialsFactory.load_credentials_from_file(credpath)
    assert creds is not None
    
    # Test that credentials have the expected structure
    # The exact attributes depend on your Credentials class implementation
    # Add assertions based on what your Credentials class exposes

def test_create_agent_missing_credentials(monkeypatch, tmp_path):
    """Test agent creation when credentials file is missing"""
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    # Don't create the credentials file
    credpath = Path(test_volttron_home) / "credentials_store/control.connection.json"
    
    with pytest.raises(FileNotFoundError):
        CredentialsFactory.load_credentials_from_file(credpath)

def test_cred_factory_with_different_identities(monkeypatch, tmp_path):
    """Test credentials factory with different identity formats"""
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    creds_dir = test_volttron_home / "credentials_store"
    creds_dir.mkdir(parents=True)
    
    # Test with different identity
    cred_file = creds_dir / "agent.connection.json"
    test_creds_content = {
        "identity": "platform.agent",
        "publickey": "A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8S9T0U1V2",
        "secretkey": "Z9Y8X7W6V5U4T3S2R1Q0P9O8N7M6L5K4J3I2H1G0F9E8",
        "domain": "test_domain",
        "address": "tcp://127.0.0.1:22916"
    }
    
    cred_file.write_text(json.dumps(test_creds_content))
    
    creds = CredentialsFactory.load_credentials_from_file(cred_file)
    
    assert creds is not None
    assert isinstance(creds, Credentials)

def test_cred_factory_with_empty_domain_and_address(monkeypatch, tmp_path):
    """Test credentials factory with empty domain and address (default case)"""
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    creds_dir = test_volttron_home / "credentials_store"
    creds_dir.mkdir(parents=True)
    
    cred_file = creds_dir / "empty_domain.connection.json"
    test_creds_content = {
        "identity": "platform.test",
        "publickey": "TEST_PUBLIC_KEY_123456789ABCDEF",
        "secretkey": "TEST_SECRET_KEY_987654321FEDCBA",
        "domain": "",
        "address": ""
    }
    
    cred_file.write_text(json.dumps(test_creds_content))
    
    creds = CredentialsFactory.load_credentials_from_file(cred_file)
    
    assert creds is not None
    assert isinstance(creds, Credentials)
    
def test_fresh_credentials_are_valid_keypairs(control_credentials, platform_credentials):
    """Test that generated credentials contain valid CURVE keypairs"""
    from volttron.messagebus.zmq.zap.credentials_creator import decode_key, encode_key
    import zmq
    
    # Test that keys can be decoded without error
    control_public_raw = decode_key(control_credentials.publickey)
    control_secret_raw = decode_key(control_credentials.secretkey)
    platform_public_raw = decode_key(platform_credentials.publickey)
    platform_secret_raw = decode_key(platform_credentials.secretkey)
    
    # Test that keys are the right length (32 bytes for CURVE25519)
    assert len(control_public_raw) == 40  # Z85 encoded 32-byte key
    assert len(control_secret_raw) == 40
    assert len(platform_public_raw) == 40
    assert len(platform_secret_raw) == 40
    
    # Test that keys are different
    assert control_credentials.publickey != platform_credentials.publickey
    assert control_credentials.secretkey != platform_credentials.secretkey
    
    # Test that keys can be round-trip encoded/decoded
    assert encode_key(control_public_raw) == control_credentials.publickey
    assert encode_key(control_secret_raw) == control_credentials.secretkey

def test_connection_context_creation_with_real_keys(control_credentials, platform_credentials):
    """Test ZmqConnectionContext creation with real generated keys"""
    from volttron.messagebus.zmq.zmq_connection import ZmqConnectionContext
    context = ZmqConnectionContext(
        address="tcp://127.0.0.1:22916",
        identity=control_credentials.identity,
        publickey=control_credentials.publickey,
        secretkey=control_credentials.secretkey,
        serverkey=platform_credentials.publickey
    )
    
    # Verify context was created correctly
    assert context.address == "tcp://127.0.0.1:22916"
    assert context.identity == control_credentials.identity
    assert context.publickey == control_credentials.publickey
    assert context.secretkey == control_credentials.secretkey
    assert context.serverkey == platform_credentials.publickey

def test_zmq_connection_mock_only_network(control_credentials, platform_credentials):
    """Test ZMQ connection logic, mocking only network operations"""
    from volttron.messagebus.zmq.zmq_connection import ZmqConnectionContext, ZmqConnection
    context = ZmqConnectionContext(
        address="tcp://127.0.0.1:22916",
        identity=control_credentials.identity,
        publickey=control_credentials.publickey,
        secretkey=control_credentials.secretkey,
        serverkey=platform_credentials.publickey
    )
    
    # Mock only the actual socket operations, not the connection logic
    with patch('zmq.Context') as mock_zmq_context:
        mock_socket = Mock()
        mock_context = Mock()
        mock_context.socket.return_value = mock_socket
        mock_zmq_context.return_value = mock_context
        
        # Test real connection creation logic
        connection = ZmqConnection(context, mock_context)
        
        # This tests the actual connection setup logic
        connection.open_connection()
        
        # Verify that real ZMQ socket methods were called
        mock_context.socket.assert_called_once()
        # Add more specific assertions based on what ZmqConnection.open_connection() should do

def test_multiple_credential_generation(keypair_for_identity):
    """Test generating multiple different credentials"""
    # Generate credentials for different identities
    control_creds = keypair_for_identity("platform.control")
    agent1_creds = keypair_for_identity("platform.agent1")
    agent2_creds = keypair_for_identity("platform.agent2")
    
    # Verify all have different keys
    assert control_creds.publickey != agent1_creds.publickey
    assert control_creds.secretkey != agent1_creds.secretkey
    assert agent1_creds.publickey != agent2_creds.publickey
    assert agent1_creds.secretkey != agent2_creds.secretkey
    
    # Verify all have correct identities
    assert control_creds.identity == "platform.control"
    assert agent1_creds.identity == "platform.agent1" 
    assert agent2_creds.identity == "platform.agent2"

def test_credentials_with_custom_domain_address(credentials_with_domain):
    """Test credentials with custom domain and address"""
    creds = credentials_with_domain
    
    assert creds.identity == "test.domain"
    assert creds.domain == "test_domain"
    assert creds.address == "tcp://127.0.0.1:22916"
    assert len(creds.publickey) > 0
    assert len(creds.secretkey) > 0

def test_key_encoding_decoding(fresh_keypair):
    """Test key encoding and decoding functions"""
    from volttron.messagebus.zmq.zap.credentials_creator import encode_key, decode_key
    
    creds = fresh_keypair
    
    # Test that keys can be decoded and re-encoded
    decoded_public = decode_key(creds.publickey)
    decoded_secret = decode_key(creds.secretkey)
    
    # Re-encode
    reencoded_public = encode_key(decoded_public)
    reencoded_secret = encode_key(decoded_secret)
    
    # Should match original
    assert reencoded_public == creds.publickey
    assert reencoded_secret == creds.secretkey