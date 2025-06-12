# test_zmq_messagebus.py
import pytest
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from volttron.messagebus.zmq import ZmqMessageBus
from volttron.types import MessageBusStopHandler

class TestZmqMessageBusInitialization:
    """Test ZmqMessageBus initialization"""
    
    def test_init_with_required_parameters(self, server_options, mock_auth_service, mock_service_notifier):
        """Test ZmqMessageBus initialization with required parameters"""
        messagebus = ZmqMessageBus(
            server_options=server_options,
            auth_service=mock_auth_service,
            notifier=mock_service_notifier
        )
        
        assert messagebus._server_options is server_options
        assert messagebus._auth_service is mock_auth_service
        assert messagebus._notifier is mock_service_notifier
        assert messagebus._thread is None
        
    def test_init_without_optional_parameters(self, server_options):
        """Test ZmqMessageBus initialization without optional parameters"""
        messagebus = ZmqMessageBus(
            server_options=server_options
        )
        
        assert messagebus._server_options is server_options
        assert messagebus._auth_service is None
        assert messagebus._notifier is None

class TestZmqMessageBusLifecycle:
    """Test ZmqMessageBus lifecycle management"""
    
    def test_start_creates_thread(self, server_options, mock_auth_service, mock_service_notifier):
        """Test that start() creates and starts router thread"""
        messagebus = ZmqMessageBus(
            server_options=server_options,
            auth_service=mock_auth_service,
            notifier=mock_service_notifier
        )
        
        with patch('threading.Thread') as mock_thread_class:
            mock_thread = Mock()
            mock_thread_class.return_value = mock_thread
            
            messagebus.start()
            
            assert messagebus._thread is mock_thread
            mock_thread.start.assert_called_once()
            
    def test_start_with_gevent_support(self, server_options):
        """Test start with GEVENT_SUPPORT environment variable"""
        messagebus = ZmqMessageBus(server_options=server_options)
        
        with patch('os.environ', {'GEVENT_SUPPORT': 'True'}) as mock_env, \
             patch('threading.Thread') as mock_thread_class:
            
            mock_thread = Mock()
            mock_thread_class.return_value = mock_thread
            
            messagebus.start()
            
            # Should temporarily remove GEVENT_SUPPORT and then restore it
            mock_thread.start.assert_called_once()
            
    def test_is_running_true(self, server_options):
        """Test is_running returns True when thread is alive"""
        messagebus = ZmqMessageBus(server_options=server_options)
        
        mock_thread = Mock()
        mock_thread.is_alive.return_value = True
        messagebus._thread = mock_thread
        
        assert messagebus.is_running() is True
        
    def test_is_running_false(self, server_options):
        """Test is_running returns False when thread is not alive"""
        messagebus = ZmqMessageBus(server_options=server_options)
        
        mock_thread = Mock()
        mock_thread.is_alive.return_value = False
        messagebus._thread = mock_thread
        
        assert messagebus.is_running() is False
        
    def test_is_running_no_thread(self, server_options):
        """Test is_running when no thread exists"""
        messagebus = ZmqMessageBus(server_options=server_options)
        
        # Should handle case where thread is None
        with pytest.raises(AttributeError):
            messagebus.is_running()
            
    def test_stop_with_handler(self, server_options):
        """Test stop method with stop handler"""
        mock_stop_handler = Mock(spec=MessageBusStopHandler)
        messagebus = ZmqMessageBus(server_options=server_options)
        messagebus._stop_handler = mock_stop_handler
        
        messagebus.stop()
        
        mock_stop_handler.message_bus_shutdown.assert_called_once()
        
    def test_stop_without_handler(self, server_options):
        """Test stop method without stop handler"""
        messagebus = ZmqMessageBus(server_options=server_options)
        messagebus._stop_handler = None
        
        # Should not raise exception
        messagebus.stop()

class TestZmqMessageBusFederationBridge:
    """Test ZmqMessageBus federation bridge functionality"""
    
    def test_create_federation_bridge_success(self, server_options):
        """Test successful creation of federation bridge"""
        messagebus = ZmqMessageBus(server_options=server_options)
        
        mock_router = Mock()
        messagebus._router_instance = mock_router
        
        with patch('volttron.messagebus.zmq.federation.ZmqFederationBridge') as mock_bridge_class:
            mock_bridge = Mock()
            mock_bridge_class.return_value = mock_bridge
            
            result = messagebus.create_federation_bridge()
            
            assert result is mock_bridge
            mock_bridge_class.assert_called_once_with(
                router=mock_router,
                auth_service=messagebus._auth_service
            )
            
    def test_create_federation_bridge_no_router(self, server_options):
        """Test federation bridge creation when router is not available"""
        messagebus = ZmqMessageBus(server_options=server_options)
        
        with pytest.raises(ValueError, match="Router instance is not available"):
            messagebus.create_federation_bridge()
            
    def test_get_router_instance_available(self, server_options):
        """Test getting router instance when available"""
        messagebus = ZmqMessageBus(server_options=server_options)
        
        mock_router = Mock()
        messagebus._router_instance = mock_router
        
        result = messagebus._get_router_instance()
        
        assert result is mock_router
        
    def test_get_router_instance_not_available(self, server_options):
        """Test getting router instance when not available"""
        messagebus = ZmqMessageBus(server_options=server_options)
        
        with pytest.raises(ValueError, match="Router instance not available"):
            messagebus._get_router_instance()

class TestZmqMessageBusIntegration:
    """Integration tests for ZmqMessageBus"""
    
    @pytest.mark.slow
    def test_start_and_stop_integration(self, server_options, mock_auth_service):
        """Integration test for starting and stopping message bus"""
        messagebus = ZmqMessageBus(
            server_options=server_options,
            auth_service=mock_auth_service
        )
        
        # Optionally set a stop handler before starting
        mock_stop_handler = Mock(spec=MessageBusStopHandler)
        messagebus.set_stop_handler(mock_stop_handler)
        
        # Start the message bus
        messagebus.start()
        
        try:
            # Give it a moment to start
            time.sleep(0.1)
            
            # Should be running
            assert messagebus.is_running()
            
        finally:
            # Stop the message bus
            messagebus.stop()
            
            # Verify stop handler was called
            mock_stop_handler.message_bus_shutdown.assert_called_once()
            
            # Give it a moment to stop
            time.sleep(0.1)