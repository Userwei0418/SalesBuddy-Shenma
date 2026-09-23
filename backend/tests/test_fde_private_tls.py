"""Real local TLS handshakes: private trust must retain hostname verification."""

import asyncio
import ssl
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from sales_backend.integrations.supreme_fde import FdeClient, FdeConfig, FdeError


@pytest.mark.asyncio
async def test_private_ca_is_scoped_and_hostname_checked(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ))
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(cert_path, key_path)
    requests = []

    async def handle(reader, writer):
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            body = b'{"user_input_form":[]}'
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0, ssl=server_tls)
    async with server:
        port = server.sockets[0].getsockname()[1]
        for host, trust, succeeds in [
            ("localhost", str(cert_path), True),
            ("localhost", "", False),
            ("127.0.0.1", str(cert_path), False),
        ]:
            config = FdeConfig(f"https://{host}:{port}/v1", "app-synthetic-tls", "agent_final",
                               ca_bundle_path=trust)
            async with FdeClient(config) as client:
                if succeeds:
                    assert await client.parameters() == {"user_input_form": []}
                else:
                    with pytest.raises(FdeError, match="transport_error"):
                        await client.parameters()
    assert len(requests) == 1


def test_missing_private_ca_fails_before_request(tmp_path):
    config = FdeConfig("https://platform.invalid/v1", "app-synthetic-tls", "agent_final",
                       ca_bundle_path=str(tmp_path / "missing.pem"))
    with pytest.raises(FileNotFoundError):
        FdeClient(config)
