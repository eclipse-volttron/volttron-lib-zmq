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
"""ZmqCoreBuilder declares the bus it serves, so volttron-core can choose it among several registered builders."""
from volttron.client import decorators
from volttron.messagebus.zmq.zmq_core import ZmqCoreBuilder


def test_the_builder_serves_zmq_under_its_class_name():
    assert ZmqCoreBuilder.messagebus == "zmq"
    assert decorators.core_builder.registry.get("ZmqCoreBuilder") is ZmqCoreBuilder
