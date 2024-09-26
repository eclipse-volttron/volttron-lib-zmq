# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2022 Battelle Memorial Institute
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

from zmq.sugar.frame import Frame
from volttron.messagebus.zmq.serialize_frames import (
    deserialize_frames,
    serialize_frames,
)
from volttron.types import Message


def test_can_deserialize_homogeneous_string():
    abc = ["alpha", "beta", "gamma"]
    frames = [Frame(x.encode("utf-8")) for x in abc]

    deserialized = deserialize_frames(frames)

    for r in range(len(abc)):
        assert abc[r] == deserialized[r], f"Element {r} is not the same."


def test_can_serialize_homogeneous_strings():
    original = ["alpha", "beta", "gamma"]
    frames = serialize_frames(original)

    for r in range(len(original)):
        assert original[r] == frames[r].bytes.decode("utf-8"), f"Element {r} is not the same."


def test_mixed_array():
    original = [
        "alpha",
        dict(alpha=5, gamma="5.0", theta=5.0),
        "gamma",
        ["from", "to", "VIP1", ["third", "level", "here", 50]],
    ]
    frames = serialize_frames(original)
    for x in frames:
        assert isinstance(x, Frame)

    after_deserialize = deserialize_frames(frames)

    for r in range(len(original)):
        assert original[r] == after_deserialize[r], f"Element {r} is not the same."

def test_only_deserialize_frames():

    frames = ['listener',
              '',
              'VIP1',
              'listener',
              '1727127367.000000.8728026698659.000000',
              'pubsub',
              'publish',
              'heartbeat/listener',
              {"bus": "", "headers": {"TimeStamp": "2024-09-23T21:36:06.763330 00:00", "min_compatible_version": "3.0", "max_compatible_version": ""}, "message": None}]

    serialized = serialize_frames(frames)
    new_frames = deserialize_frames(serialized)

    for index, ele in enumerate(frames):
        if isinstance(ele, dict):
            for k, v in ele.items():
                assert k in new_frames[index]
                assert v == new_frames[index][k]
        else:
            assert ele == new_frames[index]

def test_none_deserialization():
    data = [0, None, True, 34.2, 'This is data for sending']
    serialized = serialize_frames(data)
    new_data = deserialize_frames(serialized)

    for index, value in enumerate(data):
        if value is None:
            assert new_data[index] == ''
        else:
            assert new_data[index] == value