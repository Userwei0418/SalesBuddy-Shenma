import base64
from uuid import uuid4

import pytest
from sales_backend.security.platform_mfa import PlatformMFACipher, authenticator, match_step
from sales_backend.security.runtime_credentials import RuntimeCredentialUnavailable, CredentialCipher


def test_rfc6238_sha1_vectors_and_verification_window():
    seed=b'12345678901234567890'
    for timestamp, expected in [(59,'94287082'),(1111111109,'07081804'),(1111111111,'14050471'),
                                (1234567890,'89005924'),(2000000000,'69279037'),(20000000000,'65353130')]:
        assert authenticator(seed,8).generate(timestamp).decode()==expected
    code=authenticator(seed).generate(300).decode()
    assert match_step(seed,code,300)==10
    assert match_step(seed,code,330)==10
    assert match_step(seed,code,360) is None
    assert match_step(seed,'１２３４５６',300) is None
    assert match_step(seed,'bad',300) is None


def test_platform_seed_cipher_is_operator_and_purpose_bound():
    identifier=str(uuid4()); cipher=PlatformMFACipher('test',{'test':b'x'*32})
    seed=b'test-only-totp-seed!!'
    result=cipher.seal(identifier,seed)
    assert base64.b32encode(seed).decode() not in str(result)
    assert cipher.open(identifier,result)==seed
    with pytest.raises(RuntimeCredentialUnavailable):
        cipher.open(str(uuid4()),result)
    with pytest.raises(RuntimeCredentialUnavailable):
        PlatformMFACipher('test',{'test':b'y'*32}).open(identifier,result)


def test_platform_mfa_key_rotation_retains_old_key_readability():
    identifier=str(uuid4()); seed=b'isolated-rotation-seed'
    old=PlatformMFACipher('old',{'old':b'o'*32})
    stored=old.seal(identifier,seed)
    rotated=PlatformMFACipher('new',{'old':b'o'*32,'new':b'n'*32})
    assert rotated.open(identifier,stored)==seed
    assert rotated.seal(identifier,seed)['key_id']=='new'
    with pytest.raises(RuntimeCredentialUnavailable):
        PlatformMFACipher('new',{'new':b'n'*32}).open(identifier,stored)



def test_provider_ciphertext_is_not_accepted_as_platform_authentication_seed():
    identifier=str(uuid4()); key=b'p'*32
    provider=CredentialCipher('test',{'test':key}).encrypt(identifier,base64.b32encode(b'provider-only-secret').decode())
    record={'version':1,'key_id':'test','ciphertext':base64.b64encode(provider['api_key_ciphertext']).decode()}
    with pytest.raises(RuntimeCredentialUnavailable):
        PlatformMFACipher('test',{'test':key}).open(identifier,record)
