import pytest
import hashlib
import hmac
from pathlib import Path
import tempfile
from openpilot.sunnypilot.selfdrive.car.ioniq_speed_limit import IoniqSpeedLimitClient


class TestEventClient:
  def setup_method(self):
    self.folder = tempfile.TemporaryDirectory()
    self.key = bytes(range(32))
    self.path = Path(self.folder.name) / 'key'
    self.path.write_bytes(self.key)
    self.now = 100.0
    self.client = IoniqSpeedLimitClient(self.path, clock=lambda: self.now)
    self.token = b'1' * 32
    self.remote = b'2' * 32
    self.native = b'3' * 32
    self.client.requests[self.token] = 100000000000
    self.offset = 3600000000000
    self.ack()

  def teardown_method(self):
    self.client.close()
    self.folder.cleanup()

  def packet(self, body):
    return body + b' ' + hmac.new(self.key, body, hashlib.sha256).hexdigest().encode()

  def ack(self, received=100010000000, sent=100011000000, now=100.02):
    body = b' '.join(
      [b'SL3', b'ACK', self.token, self.client.client_session, self.remote, str(self.offset + received).encode(), str(self.offset + sent).encode()]
    )
    self.client._accept(self.packet(body), now)
    self.now = now

  def event(
    self,
    seq=1,
    observed=100019000000,
    valid=1,
    limit=50,
    position=100019000000,
    preview=0,
    ahead=0,
    distance=0,
    token=None,
    sent=100020000000,
    metadata=144,
    flag=0,
  ):
    values = [b'SL3', b'EV', token or self.token, self.client.client_session, self.remote, self.native]
    numbers = [
      seq,
      sent + self.offset,
      observed + self.offset if observed else 0,
      valid,
      limit,
      metadata,
      flag,
      position + self.offset if position else 0,
      preview,
      ahead,
      distance,
    ]
    body = b' '.join(values + [str(value).encode() for value in numbers])
    self.client._accept(self.packet(body), self.now)

  def test_large_clock_offset_current_and_preview(self):
    self.event(preview=1, ahead=90, distance=120000)
    assert self.client.limit == pytest.approx(50 / 3.6, abs=10 ** (-7), rel=0)
    assert self.client.preview()[:3] == (25.0, 120.0, True)
    assert self.client.preview()[3] > 0.05

  def test_replayed_sequence_and_token_cannot_replace(self):
    self.event()
    self.event(limit=90)
    assert self.client.limit == pytest.approx(50 / 3.6, abs=10 ** (-7), rel=0)
    self.event(seq=2, limit=90, token=b'4' * 32)
    assert self.client.limit == pytest.approx(50 / 3.6, abs=10 ** (-7), rel=0)

  def test_invalid_source_clears_both_immediately(self):
    self.event(preview=1, ahead=90, distance=10000)
    self.event(seq=2, valid=0, observed=0, position=0)
    assert self.client.limit == 0
    assert self.client.preview() == (0.0, 0.0, False, 0.0)

  def test_position_event_does_not_refresh_current(self):
    self.event()
    expiry = self.client.expires
    self.now += 0.4
    self.event(seq=2, position=100419000000, sent=100420000000)
    assert self.client.expires == pytest.approx(expiry, abs=10 ** (-7), rel=0)

  def test_clock_renewal_cannot_refresh_source(self):
    self.event()
    expiry = self.client.expires
    self.token = b'4' * 32
    self.client.requests[self.token] = 100400000000
    self.ack(100410000000, 100411000000, 100.42)
    self.event(seq=2, sent=100420000000)
    assert self.client.expires == pytest.approx(expiry, abs=10 ** (-7), rel=0)

  def test_delayed_first_packet_not_fresh(self):
    self.now = 100.8
    self.event(observed=98000000000, position=98000000000)
    assert self.client.limit == 0
    assert self.client.last_sequence == 1

  def test_preview_expiry_independent_of_current(self):
    self.now = 100.8
    self.event(observed=100790000000, sent=100800000000, position=99000000000, preview=1, ahead=90)
    assert self.client.limit > 0
    assert not self.client.preview()[2]

  def test_future_source_time_rejected(self):
    self.event(observed=100030000000)
    assert self.client.last_sequence == -1

  def test_wrong_signature_rejected(self):
    self.client._accept(b'SL3 ACK ' + b'0' * 100, self.now)
    assert self.client.last_sequence == -1

  def test_clock_lease_expiry(self):
    self.now = 102.0
    self.event(seq=1, observed=101990000000, sent=102000000000)
    assert self.client.limit == 0

  def test_cached_source_after_suspend_is_not_revived_by_new_clock_ack(self):
    self.event()
    self.token = b'4' * 32
    self.client.requests[self.token] = 3700000000000
    self.ack(3700010000000, 3700011000000, 3700.02)
    self.event(seq=2, sent=3700020000000)
    assert self.client.limit == 0
    assert not self.client.preview()[2]

  def test_server_restart_resets_source(self):
    self.event()
    self.remote = b'f' * 32
    self.token = b'a' * 32
    self.client.requests[self.token] = 100400000000
    self.ack(100410000000, 100411000000, 100.42)
    assert self.client.limit == 0
    assert self.client.last_sequence == -1

  def test_excessive_rtt_and_impossible_clock_interval(self):
    self.client.close()
    self.client.requests[self.token] = 100000000000
    self.ack(now=100.8)
    assert self.client.remote_session is None
    self.client.requests[self.token] = 100000000000
    self.ack(received=100010000000, sent=100500000000)
    assert self.client.remote_session is None

  def test_missing_key_does_not_open_a_socket(self):
    client = IoniqSpeedLimitClient(Path(self.folder.name) / 'missing')
    assert client.update() == 0.0
    assert client.sock is None

  def test_invalid_authenticated_source_fields_clear_previous_limit(self):
    sequence = 1
    for changes in ({'valid': 0}, {'limit': 0}, {'limit': 201}, {'limit': 65535}, {'metadata': 255}, {'flag': 1}, {'observed': 98000000000}):
      self.event(seq=sequence)
      assert self.client.limit == pytest.approx(50 / 3.6, abs=10 ** (-7), rel=0)
      sequence += 1
      self.event(seq=sequence, **changes)
      assert self.client.limit == 0.0
      assert not self.client.preview()[2]
      sequence += 1

  def test_legacy_protocols_cannot_replace_an_authenticated_event(self):
    self.event()
    for version in (b'SL1', b'SL2'):
      self.client._accept(self.packet(version + b' ' + self.token + b' 0 1 90 144 0'), self.now)
      assert self.client.limit == pytest.approx(50 / 3.6, abs=10 ** (-7), rel=0)
      assert self.client.last_sequence == 1

  def test_can_value_has_priority(self):
    self.event(preview=1, ahead=90, distance=10000)
    self.client.update = lambda: self.client.limit
    assert self.client.apply(25.0) == 25.0
    assert self.client.apply(0.0) == pytest.approx(50 / 3.6, abs=10 ** (-7), rel=0)

  def test_local_udp_subscription_and_pushed_event(self):
    import select
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
      server.bind(('127.0.0.1', 0))
      server.settimeout(1)
      self.client.address = server.getsockname()
      self.client.update()
      packet, address = server.recvfrom(1024)
      body, signature = packet.rsplit(b' ', 1)
      assert signature == hmac.new(self.key, body, hashlib.sha256).hexdigest().encode()
      version, action, token, session = body.split()
      assert (version, action, session) == (b'SL3', b'SUB', self.client.client_session)
      stamp = str(int(self.now * 1000000000.0) + self.offset).encode()
      ack = b' '.join((b'SL3', b'ACK', token, session, self.remote, stamp, stamp))
      event = b' '.join((b'SL3', b'EV', token, session, self.remote, self.native, b'1', stamp, stamp, b'1', b'50', b'144', b'0', stamp, b'0', b'0', b'0'))
      server.sendto(self.packet(ack), address)
      server.sendto(self.packet(event), address)
      assert select.select([self.client.sock], [], [], 1)[0]
      assert self.client.update() == pytest.approx(50 / 3.6, abs=10 ** (-7), rel=0)


"Run on Comma to verify the candidate client's real CarStateSP mapping, without control publishing."
try:
  from cereal import custom
except ImportError:
  custom = None


class TestCommaContract:
  def setup_method(self):
    self.now = 100.0
    self.client = IoniqSpeedLimitClient(key_path='/no-such-test-key', clock=lambda: self.now)
    self.client.limit = 50 / 3.6
    self.client.ahead = 90 / 3.6
    self.client.ahead_distance = 20.0
    self.client.expires = self.client.clock_expires = 101.0
    self.client.sample_time = 99.9
    self.client.update = lambda: self.client.limit

  def test_owned_current_and_preview_map_to_existing_schema(self):
    state = custom.CarStateSP.new_message()
    self.client.apply_state(state)
    assert state.speedLimit == pytest.approx(50 / 3.6, abs=1e-05, rel=0)
    assert state.speedLimitAhead == pytest.approx(90 / 3.6, abs=1e-05, rel=0)
    assert state.speedLimitAheadDistance == 20.0
    assert state.speedLimitAheadValid
    assert state.speedLimitAheadAge == pytest.approx(0.1, abs=10 ** (-7), rel=0)

  def test_real_car_data_keeps_priority_and_clears_headunit_preview(self):
    state = custom.CarStateSP.new_message(speedLimit=30 / 3.6)
    self.client.apply_state(state)
    assert state.speedLimit == pytest.approx(30 / 3.6, abs=1e-05, rel=0)
    assert not state.speedLimitAheadValid
    assert state.speedLimitAhead == 0.0

  def test_expired_position_clears_preview_without_clearing_fresh_current(self):
    self.client.sample_time = 98.0
    state = custom.CarStateSP.new_message()
    self.client.apply_state(state)
    assert state.speedLimit == pytest.approx(50 / 3.6, abs=1e-05, rel=0)
    assert not state.speedLimitAheadValid
