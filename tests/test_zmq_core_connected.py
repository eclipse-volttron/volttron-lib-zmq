# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2026 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}
"""ZmqCore.connected tracks the link to the router (volttron-lib-zmq#39).

Core's ``connected`` only says that a socket exists, which stays true after the router has gone. ZmqCore's socket
monitor records the link going down (EVENT_DISCONNECTED, or reconnecting given up) and coming back (EVENT_CONNECTED,
a hello reply) through the ``connected`` setter, which used to be a no-op.
"""
from types import SimpleNamespace

from volttron.messagebus.zmq.zmq_core import ZmqCore


def _core(socket_exists=True):
    core = ZmqCore.__new__(ZmqCore)    # the property only needs the connection and the link flag
    core._connection = SimpleNamespace(connected=socket_exists)
    core._link_down = False
    return core


def test_connected_while_the_socket_exists_and_the_link_is_not_known_down():
    assert _core().connected


def test_the_monitor_clearing_connected_is_recorded():
    core = _core()
    core.connected = False    # what the monitor does on EVENT_DISCONNECTED
    assert not core.connected
    assert core._connection.connected    # the socket is untouched


def test_the_link_coming_back_sets_connected_again():
    core = _core()
    core.connected = False
    core.connected = True     # EVENT_CONNECTED, or the router answering a hello
    assert core.connected


def test_no_socket_is_never_connected():
    core = _core(socket_exists=False)
    core.connected = True
    assert not core.connected
