import hashlib
import hmac
from pathlib import Path
import socket
import select
import tempfile
import unittest
from types import SimpleNamespace

from openpilot.sunnypilot.selfdrive.car.ioniq_speed_limit import IoniqSpeedLimitClient


class BridgeTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.key = bytes(range(32))
    path = Path(self.temp.name) / "key"
    path.write_bytes(self.key)
    self.now = 100.
    self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.server.bind(("127.0.0.1", 0))
    self.server.settimeout(1)
    self.client = IoniqSpeedLimitClient(path, self.server.getsockname(), lambda: self.now)

  def tearDown(self):
    self.client.close()
    self.server.close()
    self.temp.cleanup()

  def request(self):
    self.client.update()
    request, address = self.server.recvfrom(512)
    body, signature = request.rsplit(b" ", 1)
    self.assertEqual(signature, hmac.new(self.key, body, hashlib.sha256).hexdigest().encode())
    return body.split()[1], address

  def packet(self, nonce, fields="0 1 50 144 0", key=None, version=b"SL1", position_age=0):
    if version == b"SL2":
      fields += " " + str(position_age)
    body = version + b" " + nonce + b" " + fields.encode()
    return body + b" " + hmac.new(self.key if key is None else key, body, hashlib.sha256).hexdigest().encode()

  def test_live_udp_and_units(self):
    nonce, address = self.request()
    self.server.sendto(self.packet(nonce), address)
    self.assertTrue(select.select([self.client.sock], [], [], 1)[0])
    self.assertAlmostEqual(self.client.update(), 50 / 3.6)

  def test_timeout_expires_without_wall_clock(self):
    nonce, _ = self.request()
    self.client._accept(self.packet(nonce, "800 1 90 144 0"), self.now)
    self.assertAlmostEqual(self.client.update(), 25.)
    self.now += .701
    self.assertEqual(self.client.update(), 0.)

  def test_invalid_and_unknown_fields_clear_previous_limit(self):
    for fields in ("0 0 50 144 0", "0 1 0 144 0", "0 1 65535 144 0", "1001 1 50 144 0",
                   "-1 0 0 0 0", "0 1 50 255 0", "0 1 50 144 1", "0 1 201 144 0"):
      with self.subTest(fields=fields):
        self.now += 1
        nonce, _ = self.request()
        self.client.limit = 25.
        self.client.expires = self.now + 10
        self.client._accept(self.packet(nonce, fields), self.now)
        self.assertEqual(self.client.update(), 0.)

  def test_authentication_replay_and_late_packets(self):
    nonce, _ = self.request()
    self.client._accept(self.packet(nonce, key=b"wrong"), self.now)
    self.assertEqual(self.client.update(), 0.)
    packet = self.packet(nonce)
    self.client._accept(packet, self.now)
    self.now += 2
    self.client._accept(packet, self.now)
    self.assertEqual(self.client.update(), 0.)
    late, _ = self.server.recvfrom(512)
    late_nonce = late.split()[1]
    self.now += .8
    self.client._accept(self.packet(late_nonce), self.now)
    self.assertEqual(self.client.update(), 0.)

  def test_out_of_order_cannot_restore_previous_value(self):
    first, _ = self.request()
    self.now += .5
    second, _ = self.request()
    self.client._accept(self.packet(second, "0 0 0 255 0"), self.now)
    self.client._accept(self.packet(first), self.now)
    self.assertEqual(self.client.update(), 0.)

  def test_can_value_has_precedence(self):
    nonce, _ = self.request()
    self.client._accept(self.packet(nonce), self.now)
    self.assertEqual(self.client.apply(25.), 25.)
    self.assertAlmostEqual(self.client.apply(0.), 50 / 3.6)

  def test_missing_key_disables_link(self):
    client = IoniqSpeedLimitClient(Path(self.temp.name) / "absent")
    self.assertEqual(client.update(), 0.)
    self.assertIsNone(client.sock)

  def test_preview_units_age_and_independent_expiry(self):
    nonce, _ = self.request()
    self.client._accept(self.packet(nonce, "200 1 90 144 0 1 50 180500 0", version=b"SL2"), self.now)
    self.assertEqual(self.client.preview(), (50 / 3.6, 180.5, True, self.now - (self.now - .2)))
    self.now += .801
    self.assertFalse(self.client.preview()[2])
    self.assertEqual(self.client.update(), 25.)

  def test_preview_never_overrides_can_source(self):
    nonce, _ = self.request()
    self.client._accept(self.packet(nonce, "0 1 90 144 0 1 50 50000 0", version=b"SL2"), self.now)
    state = SimpleNamespace(speedLimit=20.)
    self.client.apply_state(state)
    self.assertEqual(state.speedLimit, 20.)
    self.assertFalse(state.speedLimitAheadValid)
    state.speedLimit = 0.
    self.client.apply_state(state)
    self.assertEqual(state.speedLimit, 25.)
    self.assertTrue(state.speedLimitAheadValid)

  def test_position_age_is_included_in_preview_freshness(self):
    nonce, _ = self.request()
    self.client._accept(self.packet(nonce, "500 1 90 144 0 1 50 50000 0", version=b"SL2", position_age=600), self.now)
    self.assertEqual(self.client.update(), 25.)
    self.assertFalse(self.client.preview()[2])

  def test_invalid_preview_retains_only_valid_current(self):
    for suffix in ("0 50 50000 0", "1 65535 50000 0", "1 50 -1 0", "1 50 1500001 0", "1 50 50000 1"):
      self.now += 1
      nonce, _ = self.request()
      self.client._accept(self.packet(nonce, "0 1 90 144 0 " + suffix, version=b"SL2"), self.now)
      self.assertEqual(self.client.update(), 25.)
      self.assertFalse(self.client.preview()[2])

  def test_legacy_response_and_invalid_current_clear_previous_preview(self):
    for fields, version in (("0 1 90 144 0", b"SL1"), ("0 0 90 144 0 1 50 50000 0", b"SL2")):
      self.now += 1
      nonce, _ = self.request()
      self.client.ahead = 50 / 3.6
      self.client._accept(self.packet(nonce, fields, version=version), self.now)
      self.assertFalse(self.client.preview()[2])


if __name__ == "__main__":
  unittest.main()
