"""Authenticated event subscription. No SL1/SL2 or external-reader fallback."""
import hashlib
import hmac
from pathlib import Path
import secrets
import socket
import time


def source_clock():
  # BOOTTIME counts suspend; host tests on macOS have no BOOTTIME clock.
  if hasattr(time, "CLOCK_BOOTTIME"):
    return time.clock_gettime(time.CLOCK_BOOTTIME)
  return time.monotonic()


class IoniqSpeedLimitClient:
  ENABLE_PREVIEW = True
  POLL_INTERVAL = 0.5  # Clock/lease renewal only; data is pushed immediately.
  MAX_AGE = 1.5
  MAX_RTT = 0.5
  CLOCK_LEASE = 1.5
  CLOCK_MARGIN_NS = 50_000_000

  def __init__(self, key_path="/data/ioniq-speed-limit/key", address=("192.168.43.2", 38472), clock=source_clock):
    self.clock, self.address = clock, address
    try:
      self.key = Path(key_path).read_bytes()
    except OSError:
      self.key = b""
    self.client_session = secrets.token_hex(16).encode()
    self.sock = None
    self.next_poll = 0.
    self.requests = {}
    self.clock_expires = 0.
    self.accepted_clock = -1
    self.remote_session = None
    self.token = None
    self.offset_low = self.offset_high = 0
    self.last_sequence = -1
    self.last_response = None
    self.last_event = None
    self._invalidate()

  def _invalidate(self):
    self.limit = self.ahead = self.ahead_distance = 0.
    self.expires = 0.
    self.sample_time = -1.

  def close(self):
    if self.sock is not None:
      self.sock.close()
    self.sock = None
    self.requests.clear()
    self.clock_expires = 0.
    self.remote_session = self.token = None
    self.last_sequence = -1
    self.last_event = None
    self._invalidate()

  def _sign(self, body):
    return hmac.new(self.key, body, hashlib.sha256).hexdigest().encode("ascii")

  @staticmethod
  def _identifier(value):
    return len(value) == 32 and all(c in b"0123456789abcdef" for c in value)

  def _accept(self, packet, now):
    try:
      if len(packet) > 1024:
        return
      body, signature = packet.rsplit(b" ", 1)
      if not hmac.compare_digest(self._sign(body), signature):
        return
      f = body.split(b" ")
      if len(f) < 2 or f[0] != b"SL3":
        return
      now_ns = int(now * 1_000_000_000)
      if f[1] == b"ACK":
        if len(f) != 7 or f[3] != self.client_session or not self._identifier(f[4]):
          return
        sent = self.requests.get(f[2])
        if sent is None or sent <= self.accepted_clock:
          return
        remote_received, remote_sent = int(f[5]), int(f[6])
        rtt_ns = now_ns - sent
        if not 0 <= rtt_ns <= int(self.MAX_RTT * 1e9) or not 0 <= remote_received <= remote_sent:
          return
        if remote_sent - remote_received > rtt_ns:
          return
        low, high = remote_sent - now_ns, remote_received - sent
        if low > high:
          return
        if self.remote_session != f[4]:
          self._invalidate()
          self.last_sequence = -1
        self.remote_session, self.token = f[4], f[2]
        self.offset_low, self.offset_high = low, high
        self.clock_expires = sent / 1e9 + self.CLOCK_LEASE
        self.accepted_clock = sent
        del self.requests[f[2]]
        return
      if f[1] != b"EV" or len(f) != 17 or now >= self.clock_expires:
        return
      if (f[2], f[3], f[4]) != (self.token, self.client_session, self.remote_session):
        return
      if not self._identifier(f[5]):
        return
      sequence, sent_ns, current_ns, valid, limit, metadata, flag, position_ns, ahead_valid, ahead, distance = map(int, f[6:])
      if not 0 <= sequence < 2**63 or sequence <= self.last_sequence or sent_ns <= 0:
        return
      # Reject a delayed packet using the conservative clock interval, not receipt age.
      packet_age_upper = now_ns - sent_ns + self.offset_high + self.CLOCK_MARGIN_NS
      packet_age_lower = now_ns - sent_ns + self.offset_low - self.CLOCK_MARGIN_NS
      if packet_age_upper < 0 or packet_age_upper > int(self.MAX_AGE * 1e9) or packet_age_lower > int(self.MAX_AGE * 1e9):
        return
      if valid not in (0, 1) or ahead_valid not in (0, 1):
        return
      if not 0 <= current_ns <= sent_ns or not 0 <= position_ns <= sent_ns:
        return
      self.last_sequence = sequence
      source_ns = max(current_ns, position_ns)
      source_age = (now_ns - source_ns + self.offset_high + self.CLOCK_MARGIN_NS) / 1e9 if source_ns else None
      self.last_response = (int(source_age * 1000) if source_age is not None else -1, valid, limit, metadata, flag)
      self.last_event = {"sequence": sequence, "sender_session": f[4].decode(),
                         "native_session": f[5].decode(), "source_age_upper_ms":
                         round(source_age * 1000, 3) if source_age is not None else None,
                         "clock_interval_ms": round((self.offset_high - self.offset_low) / 1e6, 3),
                         "current_valid": bool(valid), "raw_current_kph": limit,
                         "current_ns": current_ns, "position_ns": position_ns}
      self._invalidate()
      current_age = (now_ns - current_ns + self.offset_high + self.CLOCK_MARGIN_NS) / 1e9
      if valid != 1 or not current_ns or not 0 <= current_age < self.MAX_AGE:
        return
      if not 5 <= limit <= 200 or metadata != 144 or flag != 0:
        return
      self.limit = limit / 3.6
      self.expires = min(now + self.MAX_AGE - current_age, self.clock_expires)
      position_age = (now_ns - position_ns + self.offset_high + self.CLOCK_MARGIN_NS) / 1e9
      if ahead_valid == 1 and position_ns and 0 <= position_age <= 1.0 and 5 <= ahead <= 200 and 0 <= distance <= 1_500_000:
        self.ahead, self.ahead_distance = ahead / 3.6, distance / 1000.
        self.sample_time = now - position_age
    except (ValueError, OverflowError):
      return

  def update(self):
    now = self.clock()
    if len(self.key) != 32:
      self._invalidate()
      return 0.
    try:
      if self.sock is None and now >= self.next_poll:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.connect(self.address)
      if self.sock is not None:
        for _ in range(16):
          try:
            packet = self.sock.recv(1025)
          except BlockingIOError:
            break
          self._accept(packet, now)
        if now >= self.next_poll:
          stamp = int(now * 1e9)
          self.requests = {n: t for n, t in self.requests.items() if stamp - t <= self.MAX_RTT * 1e9}
          token = secrets.token_hex(16).encode()
          body = b"SL3 SUB " + token + b" " + self.client_session
          self.sock.send(body + b" " + self._sign(body))
          self.requests[token] = stamp
          self.next_poll = now + self.POLL_INTERVAL
    except OSError:
      self.close()
      self.next_poll = now + 2.
    if now >= self.expires or now >= self.clock_expires:
      self._invalidate()
    return self.limit

  def apply(self, car_limit):
    headunit_limit = self.update()
    return car_limit if car_limit > 0. else headunit_limit

  def preview(self):
    if not self.ENABLE_PREVIEW:
      return (0., 0., False, 0.)
    now = self.clock()
    age = now - self.sample_time
    valid = self.limit > 0. and now < self.expires and now < self.clock_expires and self.ahead > 0. and 0 <= age <= 1.
    return (self.ahead, self.ahead_distance, True, age) if valid else (0., 0., False, 0.)

  def apply_state(self, state):
    car_limit = state.speedLimit
    state.speedLimit = self.apply(car_limit)
    ahead, distance, valid, age = self.preview() if car_limit <= 0. else (0., 0., False, 0.)
    state.speedLimitAhead = ahead
    state.speedLimitAheadDistance = distance
    state.speedLimitAheadValid = valid
    state.speedLimitAheadAge = age
