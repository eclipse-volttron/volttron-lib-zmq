import base64
import binascii

from volttron.server.decorators import credentials_creator
from volttron.types.auth.auth_credentials import (Credentials, CredentialsCreator, PKICredentials)
from zmq.green import _zmq
from zmq.utils import z85


@credentials_creator
class ZapCredentialsCreator(CredentialsCreator):
    class Meta:
        name = "zmq"

    def create(self, identity: str, **kwargs) -> Credentials:
        public, secret = _zmq.curve_keypair()
        public, secret = map(encode_key, (public, secret))
        return PKICredentials(identity=identity, publickey=public, secretkey=secret)


def encode_key(key):
    '''Base64-encode and return a key in a URL-safe manner.'''
    # There is no easy way to test if key is already base64 encoded and ASCII decoded. This seems the best way.
    if len(key) % 4 != 0:
        return key
    key = key if isinstance(key, bytes) else key.encode("utf-8")
    try:
        assert len(key) in (32, 40)
    except AssertionError:
        raise AssertionError("Assertion error while encoding key:{}, len:{}".format(key, len(key)))
    if len(key) == 40:
        key = z85.decode(key)
    return base64.urlsafe_b64encode(key)[:-1].decode("ASCII")


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
