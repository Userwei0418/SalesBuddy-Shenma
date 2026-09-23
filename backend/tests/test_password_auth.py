import pytest

from sales_backend.auth.passwords import DUMMY_HASH, encode_password, validate_password, verify_password


def test_password_uses_unique_salts_and_verifies_without_plaintext():
    password = "Isolated-testing-2026"
    first, second = encode_password(password), encode_password(password)
    assert first != second and password not in first
    assert verify_password(password, first)
    assert not verify_password("Incorrect-testing-2026", first)
    assert not verify_password(password, DUMMY_HASH)


@pytest.mark.parametrize("value", ["", "short", "x" * 7, "1" * 129, "A1" * 100])
def test_password_rejects_invalid_lengths(value):
    with pytest.raises(ValueError, match="8–128"):
        validate_password(value)


@pytest.mark.parametrize("value", ["x" * 8, "1" * 128, "!" * 8, "密" * 8, "Lettersonlypassword"])
def test_password_accepts_single_character_classes_and_length_boundaries(value):
    encoded = encode_password(value)
    assert verify_password(value, encoded)
    assert not verify_password(value + "x", encoded)


@pytest.mark.parametrize("encoded", ["plaintext", "scrypt$2$8$1$AA==$AA==", "scrypt$32768$8$1$invalid$invalid"])
def test_password_corruption_fails_closed(encoded):
    assert not verify_password("Isolated-testing-2026", encoded)
