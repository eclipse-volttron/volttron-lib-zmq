"""Tests suite for `auth`."""

from pathlib import Path
import sys

source_path = Path(__file__).parent.parent.joinpath("src").as_posix()

if source_path not in sys.path:
    sys.path.insert(0, source_path)

import json

import pytest
import zmq

from unittest.mock import Mock, patch
from volttron.server.server_options import ServerOptions
from volttron.types.auth import AuthService
from volttron.types.peer import ServicePeerNotifier

# Use absolute imports instead of relative
try:
    # Try absolute import first (when tests is a package)
    from tests.fixtures.zmq_fixtures import *
    from tests.fixtures.mock_classes import *
except ImportError:
    # Fallback to local imports
    from fixtures.zmq_fixtures import *
    from fixtures.mock_classes import *

from volttron.server.server_options import ServerOptions
from volttron.types.auth import AuthService
from volttron.types.peer import ServicePeerNotifier
from volttron.messagebus.zmq.zap.credentials_creator import VolttronCredentialsCreator, encode_key, decode_key
from volttron.types.auth import VolttronCredentials

import pytest
import time
import threading
import zmq
from unittest.mock import Mock
from volttron.messagebus.zmq import ZmqMessageBus
from volttron.types.auth import AuthService
from volttron.server.server_options import ServerOptions

@pytest.fixture
def auth_enabled_server_options(isolated_credentials_env):
    """Server options with authentication enabled"""
    env = isolated_credentials_env
    
    # Use a dynamic port to avoid conflicts
    import socket
    sock = socket.socket()
    sock.bind(('', 0))
    port = sock.getsockname()[1]
    sock.close()
    
    options = ServerOptions(
        volttron_home=env["volttron_home"],
        instance_name="test_auth_platform",
        address=[f"tcp://127.0.0.1:{port}"],
        auth_enabled=True,  # Enable authentication
        server_messagebus_id="test_server",
        agent_monitor_frequency=600
    )
    
    # Add federation attributes
    options.enable_federation = False
    options.federation_url = None
    
    return {
        "options": options,
        "port": port,
        "address": f"tcp://127.0.0.1:{port}",
        "env": env
    }

@pytest.fixture
def mock_auth_service_with_creds(isolated_credentials_env):
    """Mock auth service that properly handles our test credentials"""
    env = isolated_credentials_env
    
    # Create a more realistic auth service mock
    from volttron.types.auth import AuthService
    
    class TestAuthService:
        def __init__(self, control_creds, platform_creds):
            self.control_creds = control_creds
            self.platform_creds = platform_creds
            
        def get_credentials(self, identity):
            """Return credentials for known identities"""
            if identity == "platform":
                return self.platform_creds
            elif identity == "platform.control":
                return self.control_creds
            else:
                # For unknown identities, generate temporary ones
                from volttron.messagebus.zmq.zap.credentials_creator import VolttronCredentialsCreator
                creator = VolttronCredentialsCreator()
                return creator.create(identity=identity)
        
        def authenticate_agent(self, identity, credential):
            """Authenticate agents - accept our test identities"""
            if identity in ["platform.control", "platform"]:
                return True
            return False
            
        def is_agent_authorized(self, identity, capability):
            """Authorization check"""
            return True  # Allow all for testing
    
    return TestAuthService(env["control_creds"], env["platform_creds"])

@pytest.fixture
def auth_enabled_server_options(isolated_credentials_env):
    """Server options with authentication enabled"""
    env = isolated_credentials_env
    
    # Use a dynamic port to avoid conflicts
    import socket
    sock = socket.socket()
    sock.bind(('', 0))
    port = sock.getsockname()[1]
    sock.close()
    
    # Make instance name unique for each test
    import uuid
    unique_instance = f"test_auth_platform_{uuid.uuid4().hex[:8]}"
    
    options = ServerOptions(
        volttron_home=env["volttron_home"],
        instance_name=unique_instance,  # Unique instance name
        address=[f"tcp://127.0.0.1:{port}"],
        auth_enabled=True,
        server_messagebus_id=f"test_server_{uuid.uuid4().hex[:8]}",  # Unique server ID
        agent_monitor_frequency=600
    )
    
    # Add federation attributes
    options.enable_federation = False
    options.federation_url = None
    
    return {
        "options": options,
        "port": port,
        "address": f"tcp://127.0.0.1:{port}",
        "env": env
    }

@pytest.fixture
def running_message_bus(auth_enabled_server_options, mock_auth_service_with_creds):
    """Start a real ZmqMessageBus with authentication"""
    config = auth_enabled_server_options
    
    # Create message bus
    message_bus = ZmqMessageBus(
        server_options=config["options"],
        auth_service=mock_auth_service_with_creds
    )
    
    bus_started = False
    try:
        # Start in background thread
        message_bus.start()
        
        # Wait for startup with better error handling
        startup_ok = message_bus.wait_for_startup(timeout=5.0)
        if not startup_ok:
            pytest.fail("Message bus thread failed to start within timeout")
            
        # Wait for initialization
        max_wait = 10.0
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            if message_bus.is_running():
                startup_error = message_bus.get_startup_error()
                if startup_error:
                    pytest.fail(f"Message bus failed to start: {startup_error}")
                    
                time.sleep(0.2)  # Give it a moment to fully initialize
                bus_started = True
                break
            time.sleep(0.1)
        else:
            startup_error = message_bus.get_startup_error()
            if startup_error:
                pytest.fail(f"Message bus failed to start: {startup_error}")
            else:
                pytest.fail("Message bus failed to start within timeout")
        
        yield {
            "message_bus": message_bus,
            "address": config["address"],
            "port": config["port"],
            "env": config["env"]
        }
        
    finally:
        # Cleanup
        if bus_started:
            try:
                message_bus.stop()
                time.sleep(0.5)  # Allow shutdown
            except Exception as e:
                print(f"Warning: Error stopping message bus: {e}")
                
@pytest.fixture
def credentials_creator():
    """Provide VolttronCredentialsCreator instance for generating keypairs"""
    return VolttronCredentialsCreator()

@pytest.fixture
def fresh_keypair(credentials_creator):
    """Generate a fresh CURVE keypair for testing"""
    return credentials_creator.create(identity="test_identity")

@pytest.fixture
def control_credentials(credentials_creator):
    """Generate fresh credentials for control identity"""
    return credentials_creator.create(identity="platform.control")

@pytest.fixture
def platform_credentials(credentials_creator):
    """Generate fresh credentials for platform identity"""
    return credentials_creator.create(identity="platform")

@pytest.fixture
def agent_credentials(credentials_creator):
    """Generate fresh credentials for agent identity"""
    return credentials_creator.create(identity="platform.agent")

@pytest.fixture
def credentials_with_domain(credentials_creator):
    """Generate credentials with custom domain and address"""
    return credentials_creator.create(
        identity="test.domain", 
        domain="test_domain", 
        address="tcp://127.0.0.1:22916"
    )

@pytest.fixture
def credential_files(tmp_path, control_credentials, platform_credentials):
    """Create credential files in temporary directory"""
    creds_dir = tmp_path / "credentials_store"
    creds_dir.mkdir(parents=True)
    
    # Create control credentials file
    control_file = creds_dir / "control.connection.json"
    control_data = {
        "identity": control_credentials.identity,
        "publickey": control_credentials.publickey,
        "secretkey": control_credentials.secretkey,
        "domain": control_credentials.domain,
        "address": control_credentials.address
    }
    control_file.write_text(json.dumps(control_data))
    
    # Create platform credentials file
    platform_file = creds_dir / "platform.json"
    platform_data = {
        "identity": platform_credentials.identity,
        "publickey": platform_credentials.publickey,
        "secretkey": platform_credentials.secretkey,
        "domain": platform_credentials.domain,
        "address": platform_credentials.address
    }
    platform_file.write_text(json.dumps(platform_data))
    
    return {
        "directory": creds_dir,
        "control_file": control_file,
        "platform_file": platform_file,
        "control_creds": control_credentials,
        "platform_creds": platform_credentials
    }

@pytest.fixture
def isolated_credentials_env(monkeypatch, tmp_path, credential_files):
    """Complete isolated environment with credentials"""
    test_volttron_home = tmp_path / "volttron_home"
    test_volttron_home.mkdir(parents=True)
    monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
    
    # Move credentials to the test VOLTTRON_HOME
    dest_creds_dir = test_volttron_home / "credentials_store"
    dest_creds_dir.mkdir(parents=True)
    
    # Copy credential files
    import shutil
    shutil.copy2(credential_files["control_file"], dest_creds_dir / "control.connection.json")
    shutil.copy2(credential_files["platform_file"], dest_creds_dir / "platform.json")
    
    return {
        "volttron_home": test_volttron_home,
        "credentials_dir": dest_creds_dir,
        "control_creds": credential_files["control_creds"],
        "platform_creds": credential_files["platform_creds"]
    }

@pytest.fixture
def keypair_for_identity(credentials_creator):
    """Factory fixture to generate credentials for any identity"""
    def _create_for_identity(identity: str, domain: str = "", address: str = ""):
        return credentials_creator.create(identity=identity, domain=domain, address=address)
    return _create_for_identity

@pytest.fixture(scope="session")
def zmq_context():
    """Session-scoped ZMQ context"""
    context = zmq.Context()
    yield context
    context.term()

@pytest.fixture
def isolated_volttron_env(tmp_path, monkeypatch):
    """Provide isolated VOLTTRON environment for testing"""
    test_home = tmp_path / "volttron_home"
    test_home.mkdir(parents=True)
    
    # Set clean environment
    monkeypatch.setenv("VOLTTRON_HOME", str(test_home))
    
    # Clear potentially interfering variables
    for var in ["VOLTTRON_INSTANCE", "MESSAGEBUS", "GEVENT_SUPPORT"]:
        monkeypatch.delenv(var, raising=False)
    
    return test_home

@pytest.fixture
def server_options_clean(isolated_volttron_env):
    """ServerOptions with completely clean environment"""
    with patch('socket.gethostname', return_value='test-platform'):
        return ServerOptions()

@pytest.fixture
def temp_volttron_home(tmp_path):
    """Temporary VOLTTRON_HOME directory"""
    volttron_home = tmp_path / "volttron_home"
    volttron_home.mkdir(parents=True)
    (volttron_home / "run").mkdir()
    return volttron_home

@pytest.fixture
def server_options(temp_volttron_home):
    """Test server options with correct parameters"""
    # Create ServerOptions with only the parameters it actually accepts
    options = ServerOptions(
        volttron_home=temp_volttron_home,
        instance_name="test_platform",
        address=["tcp://127.0.0.1:22916"],
        auth_enabled=False,
        server_messagebus_id="test_server",
        agent_monitor_frequency=600,
        # Remove enable_federation and federation_url - they should have defaults
        service_address="ipc://test_service"
    )
    
    # Add federation attributes after creation if they don't exist as constructor params
    if not hasattr(options, 'enable_federation'):
        options.enable_federation = False
    if not hasattr(options, 'federation_url'):
        options.federation_url = None
    
    return options

@pytest.fixture
def server_options_with_federation(temp_volttron_home):
    """Test server options with federation enabled"""
    options = ServerOptions(
        volttron_home=temp_volttron_home,
        instance_name="test_federation_platform", 
        address=["tcp://127.0.0.1:22917"],
        auth_enabled=True,
        server_messagebus_id="test_server",
        agent_monitor_frequency=600,
        service_address="ipc://test_service"
    )
    
    # Set federation attributes after creation
    options.enable_federation = True
    options.federation_url = "http://localhost:8080/federation"
    
    return options

@pytest.fixture
def mock_auth_service():
    """Mock auth service"""
    auth_service = Mock(spec=AuthService)
    mock_creds = Mock()
    mock_creds.publickey = "test_public_key"
    mock_creds.secretkey = "test_secret_key" 
    auth_service.get_credentials.return_value = mock_creds
    return auth_service

@pytest.fixture
def mock_service_notifier():
    """Mock service peer notifier"""
    return Mock(spec=ServicePeerNotifier)

@pytest.fixture
def test_frames():
    """Standard test message frames"""
    return [
        b"sender_id",
        b"recipient_id", 
        b"VIP1",
        b"auth_token",
        b"msg_id_123",
        b"hello"
    ]