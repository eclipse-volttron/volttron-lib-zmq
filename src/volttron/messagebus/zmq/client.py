# # -*- coding: utf-8 -*- {{{
# # vim: set fenc=utf-8 ft=python sw=4 ts=4 sts=4 et:
# #
# # Copyright 2020, Battelle Memorial Institute.
# #
# # Licensed under the Apache License, Version 2.0 (the "License");
# # you may not use this file except in compliance with the License.
# # You may obtain a copy of the License at
# #
# # http://www.apache.org/licenses/LICENSE-2.0
# #
# # Unless required by applicable law or agreed to in writing, software
# # distributed under the License is distributed on an "AS IS" BASIS,
# # WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# # See the License for the specific language governing permissions and
# # limitations under the License.
# #
# # This material was prepared as an account of work sponsored by an agency of
# # the United States Government. Neither the United States Government nor the
# # United States Department of Energy, nor Battelle, nor any of their
# # employees, nor any jurisdiction or organization that has cooperated in the
# # development of these materials, makes any warranty, express or
# # implied, or assumes any legal liability or responsibility for the accuracy,
# # completeness, or usefulness or any information, apparatus, product,
# # software, or process disclosed, or represents that its use would not infringe
# # privately owned rights. Reference herein to any specific commercial product,
# # process, or services by trade name, trademark, manufacturer, or otherwise
# # does not necessarily constitute or imply its endorsement, recommendation, or
# # favoring by the United States Government or any agency thereof, or
# # Battelle Memorial Institute. The views and opinions of authors expressed
# # herein do not necessarily state or reflect those of the
# # United States Government or any agency thereof.
# #
# # PACIFIC NORTHWEST NATIONAL LABORATORY operated by
# # BATTELLE for the UNITED STATES DEPARTMENT OF ENERGY
# # under Contract DE-AC05-76RL01830
# # }}}

# import bisect
# import logging
# import os
# import random
# import uuid
# from dataclasses import dataclass
# from urllib.parse import parse_qs, urlparse, urlsplit, urlunsplit

# import gevent
# import gevent.time
# # from volttron.platform.agent.utils import (get_fq_identity, get_platform_instance_name)
# # from volttron.platform.auth import (BaseAuthentication, BaseClientAuthorization, BaseServerAuthentication)
# # from volttron.platform.auth.auth_entry import AuthEntry
# # from volttron.platform.auth.auth_exception import AuthException
# # from volttron.platform.auth.auth_utils import dump_user
# # from volttron.types import ConnectionParameters
# from volttron.utils import jsonapi
# # from volttron.platform.auth.auth_protocols import *
# # from volttron.platform import get_home
# from volttron.utils.context import ClientContext as cc
# # from volttron.platform.parameters import Parameters
# from volttron.utils.keystore import KeyStore, KnownHostsStore, encode_key
# from zmq import green as zmq

# _log = logging.getLogger(__name__)


# @dataclass
# class ZMQClientParameters:
#     publickey: str = None
#     secretkey: str = None
#     serverkey: str = None


# # ZMQAuthorization(BaseAuthorization)
# # ZMQClientAuthentication(BaseAuthentication) - Client create and verify keys
# # ZMQServerAuthentication(object) - Zap loop (Auth File Entries)
# # ZMQServerAuthorization - Approve, deny, delete certs
# # ZMQParameters(Parameters)
# class ZMQClientAuthentication(BaseAuthentication):

#     def __init__(self, params):
#         self.params = params
#         self.address = self.params.address
#         self.identity = self.params.identity
#         self.agent_uuid = self.params.agent_uuid
#         self.publickey = self.params.publickey
#         self.secretkey = self.params.secretkey
#         self.serverkey = self.params.serverkey
#         self.volttron_home = self.params.volttron_home

#     # Make Common (set_parameters? - use Parameters class)
#     def create_authentication_parameters(self):
#         """Implements logic for setting encryption keys and putting
#         those keys in the parameters of the VIP address
#         """
#         self._set_server_key()
#         self._set_public_and_secret_keys()

#         if self.publickey and self.secretkey and self.serverkey:
#             self._add_keys_to_addr()
#         return self.address

#     def _add_keys_to_addr(self):
#         '''Adds public, secret, and server keys to query in VIP address if
#         they are not already present'''

#         def add_param(query_str, key, value):
#             query_dict = parse_qs(query_str)
#             if not value or key in query_dict:
#                 return ''
#             # urlparse automatically adds '?', but we need to add the '&'s
#             return '{}{}={}'.format('&' if query_str else '', key, value)

#         url = list(urlsplit(self.address))

#         if url[0] in ['tcp', 'ipc']:
#             url[3] += add_param(url[3], 'publickey', self.publickey)
#             url[3] += add_param(url[3], 'secretkey', self.secretkey)
#             url[3] += add_param(url[3], 'serverkey', self.serverkey)
#             self.address = str(urlunsplit(url))

#     def _get_keys_from_keystore(self):
#         '''Returns agent's public and secret key from keystore'''
#         if self.agent_uuid:
#             # this is an installed agent, put keystore in its dist-info
#             current_directory = os.path.abspath(os.curdir)
#             keystore_dir = os.path.join(current_directory, "{}.dist-info".format(os.path.basename(current_directory)))
#         elif self.identity is None:
#             raise ValueError("Agent's VIP identity is not set")
#         else:
#             if not self.volttron_home:
#                 raise ValueError('VOLTTRON_HOME must be specified.')
#             keystore_dir = os.path.join(self.volttron_home, 'keystores', self.identity)

#         keystore_path = os.path.join(keystore_dir, 'keystore.json')
#         keystore = KeyStore(keystore_path)
#         return keystore.public, keystore.secret

#     def _set_public_and_secret_keys(self):
#         if self.publickey is None or self.secretkey is None:
#             self.publickey, self.secretkey, _ = self._get_keys_from_addr()
#         if self.publickey is None or self.secretkey is None:
#             self.publickey, self.secretkey = self._get_keys_from_keystore()

#     def _set_server_key(self):
#         if self.serverkey is None:
#             self.serverkey = self._get_keys_from_addr()[2]
#         known_serverkey = self._get_serverkey_from_known_hosts()

#         if (self.serverkey is not None and known_serverkey is not None and self.serverkey != known_serverkey):
#             raise Exception("Provided server key ({}) for {} does "
#                             "not match known serverkey ({}).".format(self.serverkey, self.address, known_serverkey))

#         # Until we have containers for agents we should not require all
#         # platforms that connect to be in the known host file.
#         # See issue https://github.com/VOLTTRON/volttron/issues/1117
#         if known_serverkey is not None:
#             self.serverkey = known_serverkey

#     def _get_serverkey_from_known_hosts(self):
#         known_hosts_file = os.path.join(self.volttron_home, 'known_hosts')
#         known_hosts = KnownHostsStore(known_hosts_file)
#         return known_hosts.serverkey(self.address)

#     def _get_keys_from_addr(self):
#         url = list(urlsplit(self.address))
#         query = parse_qs(url[3])
#         publickey = query.get('publickey', [None])[0]
#         secretkey = query.get('secretkey', [None])[0]
#         serverkey = query.get('serverkey', [None])[0]
#         return publickey, secretkey, serverkey


# class ZMQClientAuthorization(BaseClientAuthorization):

#     def __init__(self, auth_service):
#         super().__init__(auth_service)
