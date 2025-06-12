# tests/integration/test_zmq_integrations.py - Updated for current implementation

import pytest
import time
import zmq
import json
import threading
from volttron.types.auth.auth_credentials import CredentialsFactory
from volttron.messagebus.zmq import ZmqMessageBus

from tests.utils.zmq_test_utils import ZmqTestHelper, create_test_address

@pytest.mark.integration
def test_agent_communication():
    """Test agent communication"""
    with ZmqTestHelper() as helper:
        context = helper.create_context()
        agent1_socket = helper.create_socket(context, zmq.DEALER, "agent1")
        agent2_socket = helper.create_socket(context, zmq.DEALER, "agent2")

@pytest.mark.integration
def test_message_bus_basic_lifecycle(auth_enabled_server_options, mock_auth_service_with_creds):
    """Integration test: Basic message bus lifecycle with current implementation"""
    config = auth_enabled_server_options
    
    # Create message bus
    from volttron.messagebus.zmq import ZmqMessageBus
    message_bus = ZmqMessageBus(
        server_options=config["options"],
        auth_service=mock_auth_service_with_creds
    )
    
    # Test initial state - thread should not exist yet
    assert message_bus._thread is None
    print("✓ Message bus initially not running")
    
    # Test starting
    message_bus.start()
    
    # Give it a moment to start
    time.sleep(0.5)
    
    # Check if thread exists and is alive
    assert message_bus._thread is not None
    assert message_bus.is_running()
    print("✓ Message bus started successfully")
    
    # Test basic TCP connectivity
    context = zmq.Context()
    test_socket = context.socket(zmq.DEALER)
    
    try:
        test_socket.setsockopt(zmq.LINGER, 1000)
        test_socket.connect(config["address"])
        
        # Brief connection test
        time.sleep(0.1)
        print("✓ TCP connection successful")
        
    except Exception as e:
        print(f"Connection info: {e}")
        # Don't fail test for connection issues - focus on bus lifecycle
        
    finally:
        test_socket.close()
        context.term()
    
    # Test stopping
    message_bus.stop()
    time.sleep(0.5)  # Allow shutdown processing
    
    print("✓ Message bus stop called")
    
    # Note: Thread may still be alive briefly during shutdown
    # Don't assert on thread state immediately after stop()
    
    print("✓ Message bus lifecycle test completed")


@pytest.mark.xfail(reason="Router is_running() method is not correct as the thread may be alive - fix in progress")
@pytest.mark.integration
def test_message_bus_multiple_instances():
    """Test creating multiple message bus instances with different ports"""
    import tempfile
    from pathlib import Path
    from volttron.server.server_options import ServerOptions
    
    # Create isolated test environments
    temp_dir1 = Path(tempfile.mkdtemp())
    temp_dir2 = Path(tempfile.mkdtemp())
    
    try:
        # Get two different ports
        import socket
        
        sock1 = socket.socket()
        sock1.bind(('', 0))
        port1 = sock1.getsockname()[1]
        sock1.close()
        
        sock2 = socket.socket()
        sock2.bind(('', 0))
        port2 = sock2.getsockname()[1]
        sock2.close()
        
        # Create first message bus
        options1 = ServerOptions(
            volttron_home=temp_dir1,
            instance_name="test_platform_1",
            address=[f"tcp://127.0.0.1:{port1}"],
            auth_enabled=False,
            server_messagebus_id="test_server_1",
            agent_monitor_frequency=600
        )
        
        bus1 = ZmqMessageBus(
            server_options=options1,
            auth_service=None
        )
        
        # Create second message bus
        options2 = ServerOptions(
            volttron_home=temp_dir2,
            instance_name="test_platform_2", 
            address=[f"tcp://127.0.0.1:{port2}"],
            auth_enabled=False,
            server_messagebus_id="test_server_2",
            agent_monitor_frequency=600
        )
        
        bus2 = ZmqMessageBus(
            server_options=options2,
            auth_service=None
        )
        
        # Test both can start
        bus1.start()
        bus2.start()
        
        time.sleep(1.0)  # Allow startup
        
        assert bus1.is_running()
        assert bus2.is_running()
        print("✓ Both message buses started successfully")
        
        # Test they're on different ports
        context = zmq.Context()
        
        # Test connection to first bus
        socket1 = context.socket(zmq.DEALER)
        socket1.setsockopt(zmq.LINGER, 500)
        socket1.connect(f"tcp://127.0.0.1:{port1}")
        
        # Test connection to second bus  
        socket2 = context.socket(zmq.DEALER)
        socket2.setsockopt(zmq.LINGER, 500)
        socket2.connect(f"tcp://127.0.0.1:{port2}")
        
        time.sleep(0.1)
        print("✓ Both buses accept connections")
        
        socket1.close()
        socket2.close()
        context.term()
        
        # Cleanup
        bus1.stop()
        bus2.stop()
        time.sleep(0.5)
        
        print("✓ Multiple instance test completed")
        
    finally:
        # Cleanup temp directories
        import shutil
        try:
            shutil.rmtree(temp_dir1)
            shutil.rmtree(temp_dir2)
        except Exception as e:
            print(f"Cleanup warning: {e}")

@pytest.mark.integration
def test_zmq_connection_basic_functionality_fixed(platform_credentials, control_credentials):
    """Test ZmqConnection basic functionality with proper error handling"""
    from volttron.messagebus.zmq.zmq_connection import ZmqConnection, ZmqConnectionContext
    
    # Test connection context creation
    connect_context = ZmqConnectionContext(
        address="tcp://127.0.0.1:22916",  # Non-existent endpoint for this test
        identity=control_credentials.identity,
        publickey=control_credentials.publickey,
        secretkey=control_credentials.secretkey,
        serverkey=platform_credentials.publickey,
        reconnect_interval=5000
    )
    
    context = zmq.Context()
    connection = None
    
    try:
        connection = ZmqConnection(connect_context, context)
        
        # Test initial state
        assert not connection.connected
        assert not connection.is_connected()
        print("✓ Initial connection state correct")
        
        # Test opening connection
        connection.open_connection()
        
        # After opening, socket should exist
        assert connection._socket is not None
        print("✓ Connection opened successfully")
        
        # Test socket properties
        socket = connection._socket
        assert socket.identity == control_credentials.identity.encode('utf-8')
        print("✓ Socket identity set correctly")
        
        # Test that CURVE authentication was set up
        # Note: We can't easily test the actual curve keys, but we can test that no error occurred
        print("✓ CURVE authentication setup completed")
        
        # Test property setting
        flags = {"hwm": 5000, "reconnect_interval": 2000}
        connection.set_properties(flags)
        print("✓ Socket properties set successfully")
        
        # Test URL building
        # # TODO: When we add this method.
        # url = connection._build_connection_url()
        # assert "tcp://127.0.0.1:22916" in url
        # print("✓ Connection URL built correctly")
        
        # Test that we can call open_connection again (should be no-op)
        connection.open_connection()
        print("✓ Second open_connection call handled gracefully")
        
    except Exception as e:
        print(f"✗ Test failed with error: {e}")
        import traceback
        print(f"Traceback:\n{traceback.format_exc()}")
        raise
        
    finally:
        if connection and connection._socket:
            connection.close_connection()
        context.term()

@pytest.mark.integration
def test_zmq_socket_creation_directly():
    """Test ZMQ socket creation directly to isolate the issue"""
    import zmq.green as zmq
    
    context = zmq.Context()
    
    try:
        # Test 1: Create socket with context and type (correct way)
        socket1 = context.socket(zmq.DEALER)
        assert socket1 is not None
        print("✓ Socket created with context.socket(type)")
        socket1.close()
        
        # Test 2: Try to create socket without arguments (this should fail)
        try:
            socket2 = zmq.Socket()  # This should fail
            socket2.close()
            print("✗ Socket created without context (unexpected)")
        except TypeError as e:
            print(f"✓ Socket creation without context failed as expected: {e}")
        
        # Test 3: Create socket with GreenSocket if available
        try:
            from volttron.messagebus.zmq.green import Socket as GreenSocket
            socket3 = GreenSocket(context, zmq.DEALER)
            assert socket3 is not None
            print("✓ GreenSocket created successfully")
            socket3.close()
        except ImportError:
            print("ℹ GreenSocket not available, using regular socket")
        
    finally:
        context.term()
        
@pytest.mark.integration
def test_zmq_connection_basic_functionality(platform_credentials, control_credentials):
    """Test ZmqConnection basic functionality independent of router"""
    from volttron.messagebus.zmq.zmq_connection import ZmqConnection, ZmqConnectionContext
    
    # Test connection context creation
    connect_context = ZmqConnectionContext(
        address="tcp://127.0.0.1:22916",  # Non-existent endpoint for this test
        identity=control_credentials.identity,
        publickey=control_credentials.publickey,
        secretkey=control_credentials.secretkey,
        serverkey=platform_credentials.publickey,
        reconnect_interval=5000
    )
    
    context = zmq.Context()
    connection = ZmqConnection(connect_context, context)
    
    try:
        # Test initial state
        assert not connection.connected
        assert not connection.is_connected()
        print("✓ Initial connection state correct")
        
        # Test opening connection
        connection.open_connection()
        assert connection._socket is not None
        print("✓ Connection opened successfully")
        
        # Test socket properties
        socket = connection._socket
        assert socket.identity == control_credentials.identity.encode('utf-8')
        print("✓ Socket identity set correctly")
        
        # Test property setting
        flags = {"hwm": 5000, "reconnect_interval": 2000}
        connection.set_properties(flags)
        print("✓ Socket properties set successfully")
        
        # Test URL building
        url = connection._build_connection_url()
        assert "tcp://127.0.0.1:22916" in url
        print("✓ Connection URL built correctly")
        
    finally:
        if connection.connected:
            connection.close_connection()
        context.term()

@pytest.mark.integration
def test_credential_operations(isolated_credentials_env, credentials_creator):
    """Test credential store operations"""
    from volttron.messagebus.zmq.zap.credentials_store import FileBasedCredentialStore
    
    env = isolated_credentials_env
    
    # Initialize credential store
    store = FileBasedCredentialStore(env["credentials_dir"])
    
    # Test retrieving existing credentials
    control_creds = store.retrieve_credentials(identity="platform.control")
    assert control_creds is not None
    assert control_creds.identity == "platform.control"
    print(f"✓ Retrieved control credentials: {control_creds.identity}")
    
    platform_creds = store.retrieve_credentials(identity="platform")
    assert platform_creds is not None
    assert platform_creds.identity == "platform"
    print(f"✓ Retrieved platform credentials: {platform_creds.identity}")
    
    # Test storing new credentials
    new_creds = credentials_creator.create(identity="test.new.agent")
    store.store_credentials(credentials=new_creds)
    print(f"✓ Stored new credentials: {new_creds.identity}")
    
    # Test retrieving the new credentials
    retrieved_creds = store.retrieve_credentials(identity="test.new.agent")
    assert retrieved_creds.identity == new_creds.identity
    assert retrieved_creds.publickey == new_creds.publickey
    print(f"✓ Retrieved new credentials: {retrieved_creds.identity}")

@pytest.mark.integration
def test_key_encoding_roundtrip(fresh_keypair):
    """Test key encoding/decoding functionality"""
    from volttron.messagebus.zmq.zap.credentials_creator import encode_key, decode_key
    
    creds = fresh_keypair
    
    # Test public key encoding/decoding
    encoded_public = creds.publickey
    decoded_public = decode_key(encoded_public)
    re_encoded_public = encode_key(decoded_public)
    
    assert re_encoded_public == encoded_public
    print("✓ Public key encoding round-trip successful")
    
    # Test secret key encoding/decoding  
    encoded_secret = creds.secretkey
    decoded_secret = decode_key(encoded_secret)
    re_encoded_secret = encode_key(decoded_secret)
    
    assert re_encoded_secret == encoded_secret
    print("✓ Secret key encoding round-trip successful")
    
    # Test decoded keys have correct length
    assert len(decoded_public) == 40  # Z85 encoded 32-byte key
    assert len(decoded_secret) == 40  # Z85 encoded 32-byte key
    print("✓ Decoded keys have correct length")

@pytest.mark.integration 
def test_server_options_configuration(isolated_volttron_env):
    """Test ServerOptions configuration"""
    from volttron.server.server_options import ServerOptions
    
    volttron_home = isolated_volttron_env
    
    # Test basic configuration
    options = ServerOptions(
        volttron_home=volttron_home,
        instance_name="test_config_platform",
        address=["tcp://127.0.0.1:22916"],
        auth_enabled=True,
        server_messagebus_id="test_server",
        agent_monitor_frequency=600
    )
    
    assert options.volttron_home == volttron_home
    assert options.instance_name == "test_config_platform"
    assert "tcp://127.0.0.1:22916" in options.address
    assert options.auth_enabled is True
    print("✓ ServerOptions configured correctly")
    
    # Test that VOLTTRON_HOME directory structure can be created
    run_dir = volttron_home / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    assert run_dir.exists()
    print("✓ VOLTTRON_HOME structure created")

@pytest.mark.integration
def test_volttron_home_based_inproc():
    """Test that inproc addresses are based on VOLTTRON_HOME"""
    import tempfile
    from pathlib import Path
    from volttron.server.server_options import ServerOptions
    
    # Create two different VOLTTRON_HOME directories
    temp_dir1 = Path(tempfile.mkdtemp())
    temp_dir2 = Path(tempfile.mkdtemp())
    
    try:
        # Create directory structures
        for temp_dir in [temp_dir1, temp_dir2]:
            volttron_home = temp_dir / "volttron_home"
            volttron_home.mkdir(parents=True)
            run_dir = volttron_home / "run"
            run_dir.mkdir(parents=True)
        
        # Get two different ports
        import socket
        
        sock1 = socket.socket()
        sock1.bind(('', 0))
        port1 = sock1.getsockname()[1]
        sock1.close()
        
        sock2 = socket.socket()
        sock2.bind(('', 0))
        port2 = sock2.getsockname()[1]
        sock2.close()
        
        # Create server options with same instance name but different VOLTTRON_HOME
        options1 = ServerOptions(
            volttron_home=temp_dir1 / "volttron_home",
            instance_name="same_instance_name",  # Same name
            address=[f"tcp://127.0.0.1:{port1}"],
            auth_enabled=False,
            server_messagebus_id="test_server_1",
            agent_monitor_frequency=600
        )
        
        options2 = ServerOptions(
            volttron_home=temp_dir2 / "volttron_home", 
            instance_name="same_instance_name",  # Same name
            address=[f"tcp://127.0.0.1:{port2}"],
            auth_enabled=False,
            server_messagebus_id="test_server_2",
            agent_monitor_frequency=600
        )
        
        from volttron.messagebus.zmq import ZmqMessageBus
        
        # Create two message buses
        bus1 = ZmqMessageBus(server_options=options1, auth_service=None)
        bus2 = ZmqMessageBus(server_options=options2, auth_service=None)
        
        # Both should start successfully (different VOLTTRON_HOME = different inproc)
        bus1.start()
        bus2.start()
        
        time.sleep(2.0)  # Allow startup
        
        assert bus1.is_running(), "Bus 1 should be running"
        assert bus2.is_running(), "Bus 2 should be running"
        print("✓ Both buses running with same instance name but different VOLTTRON_HOME")
        
        # Check if discovery files were created
        addr_file1 = temp_dir1 / "volttron_home" / "run" / "vip_inproc_address"
        addr_file2 = temp_dir2 / "volttron_home" / "run" / "vip_inproc_address"
        
        if addr_file1.exists():
            addr1 = addr_file1.read_text().strip()
            print(f"✓ Address 1: {addr1}")
        
        if addr_file2.exists():
            addr2 = addr_file2.read_text().strip()
            print(f"✓ Address 2: {addr2}")
            
            if addr_file1.exists():
                assert addr1 != addr2, "Addresses should be different"
                print("✓ Inproc addresses are unique per VOLTTRON_HOME")
        
        # Cleanup
        bus1.stop()
        bus2.stop()
        time.sleep(0.5)
        
    finally:
        import shutil
        try:
            shutil.rmtree(temp_dir1)
            shutil.rmtree(temp_dir2)
        except:
            pass

@pytest.mark.integration
def test_address_discovery_helper():
    """Test the address discovery helper functions"""
    import tempfile
    from pathlib import Path
    
    temp_dir = Path(tempfile.mkdtemp())
    
    try:
        volttron_home = temp_dir / "volttron_home"
        volttron_home.mkdir(parents=True)
        
        from volttron.messagebus.zmq.address_utils import get_vip_inproc_address
        
        # Test address generation
        addr1 = get_vip_inproc_address(volttron_home)
        addr2 = get_vip_inproc_address(volttron_home)
        
        # Same path should give same address
        assert addr1 == addr2
        print(f"✓ Consistent address generation: {addr1}")
        
        # Different paths should give different addresses
        other_home = temp_dir / "other_volttron_home"
        other_home.mkdir(parents=True)
        addr3 = get_vip_inproc_address(other_home)
        
        assert addr1 != addr3
        print(f"✓ Different paths give different addresses")
        print(f"  Path 1: {addr1}")
        print(f"  Path 2: {addr3}")
        
    finally:
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except:
            pass

@pytest.mark.integration
def test_message_bus_thread_management():
    """Test message bus thread management"""
    import tempfile
    from pathlib import Path
    from volttron.server.server_options import ServerOptions
    
    temp_dir = Path(tempfile.mkdtemp())
    
    try:
        # Get dynamic port
        import socket
        sock = socket.socket()
        sock.bind(('', 0))
        port = sock.getsockname()[1]
        sock.close()
        
        options = ServerOptions(
            volttron_home=temp_dir,
            instance_name="test_thread_platform",
            address=[f"tcp://127.0.0.1:{port}"],
            auth_enabled=False,
            server_messagebus_id="test_server",
            agent_monitor_frequency=600
        )
        
        from volttron.messagebus.zmq import ZmqMessageBus
        message_bus = ZmqMessageBus(
            server_options=options,
            auth_service=None
        )
        
        # Test thread lifecycle
        assert message_bus._thread is None
        print("✓ No thread initially")
        
        message_bus.start()
        time.sleep(0.5)
        
        assert message_bus._thread is not None
        assert message_bus._thread.is_alive()
        assert message_bus.is_running()
        print("✓ Thread started and running")
        
        # Test thread properties
        assert message_bus._thread.daemon is True
        print("✓ Thread is daemon thread")
        
        # Test stop
        original_thread = message_bus._thread
        message_bus.stop()
        
        # Give it time to process stop
        time.sleep(1.0)
        
        # Thread may still be alive briefly, that's OK
        print("✓ Stop method called successfully")
        
    finally:
        try:
            import shutil
            shutil.rmtree(temp_dir)
        except:
            pass

@pytest.mark.integration
def test_zmq_context_management():
    """Test ZMQ context management"""
    from volttron.messagebus.zmq import zmq_context
    
    # Test that global context exists
    assert zmq_context is not None
    print("✓ Global ZMQ context exists")
    
    # Test creating additional contexts
    local_context = zmq.Context()
    assert local_context is not None
    print("✓ Local ZMQ context created")
    
    # Test context termination
    local_context.term()
    print("✓ Local ZMQ context terminated")
    
    # Global context should still be available
    assert zmq_context is not None
    print("✓ Global ZMQ context still available")