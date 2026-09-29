"""Authenticated, nonblocking head-unit speed limits, expressed in m/s."""

import hashlib
import hmac
from pathlib import Path
import secrets
import socket
import time


class IoniqSpeedLimitClient:
  POLL_INTERVAL = 0.5
  MAX_AGE = 1.5
  MAX_RTT = 0.75

  def __init__(self, key_path="/data/ioniq-speed-limit/key", address=("192.168.43.2", 38472), clock=time.monotonic):
    self.clock = clock
    self.address = address
    try:
      self.key = Path(key_path).read_bytes()
    except OSError:
      self.key = b""
    self.sock = None
    self.next_poll = 0.
    self.requests = {}
    self.accepted_request = -1.
    self.expires = 0.
    self.limit = 0.
    self.last_response = None
    self.ahead = 0.
    self.ahead_distance = 0.
    self.sample_time = -1.

  def close(self):
    if self.sock is not None:
      self.sock.close()
    self.sock = None
    self.requests.clear()
    self.limit = 0.
    self.expires = 0.
    self.ahead = 0.
    self.ahead_distance = 0.
    self.sample_time = -1.

  def _sign(self, body):
    return hmac.new(self.key, body, hashlib.sha256).hexdigest().encode("ascii")

  def _accept(self, packet, now):
    try:
      body, signature = packet.rsplit(b" ", 1)
      if not hmac.compare_digest(self._sign(body), signature):
        return
      fields = body.split()
      if len(fields) not in (7, 12):
        return
      version, nonce, age, active, limit, metadata, flag = fields[:7]
      if (version, len(fields)) not in ((b"SL1", 7), (b"SL2", 12)):
        return
      sent = self.requests.get(nonce)
      if sent is None or not 0 <= now - sent <= self.MAX_RTT or sent <= self.accepted_request:
        return
      age, active, limit, metadata, flag = map(int, (age, active, limit, metadata, flag))
      ahead_valid, ahead, distance_mm, reason, position_age = map(int, fields[7:]) if version == b"SL2" else (0, 0, 0, 1, 1001)
    except (ValueError, OverflowError):
      return
    del self.requests[nonce]
    self.accepted_request = sent
    self.last_response = (age, active, limit, metadata, flag)
    self.limit = 0.
    self.expires = 0.
    self.ahead = 0.
    self.ahead_distance = 0.
    self.sample_time = -1.
    if 0 <= age <= 1000 and active == 1 and 5 <= limit <= 200 and metadata == 144 and flag == 0:
      self.limit = limit / 3.6
      self.expires = sent + self.MAX_AGE - age / 1000.
      self.sample_time = sent - (age + position_age) / 1000.
      if ahead_valid == 1 and reason == 0 and 0 <= position_age <= 1000 and 5 <= ahead <= 200 and 0 <= distance_mm <= 1500000:
        self.ahead = ahead / 3.6
        self.ahead_distance = distance_mm / 1000.

  def update(self):
    now = self.clock()
    if len(self.key) != 32:
      return 0.
    try:
      if self.sock is None and now >= self.next_poll:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.connect(self.address)
      if self.sock is not None:
        for _ in range(4):
          try:
            packet = self.sock.recv(512)
          except BlockingIOError:
            break
          self._accept(packet, now)
        if now >= self.next_poll:
          self.requests = {nonce: sent for nonce, sent in self.requests.items() if now - sent <= self.MAX_RTT}
          nonce = secrets.token_hex(16).encode("ascii")
          body = b"SL2 " + nonce
          self.sock.send(body + b" " + self._sign(body))
          self.requests[nonce] = now
          self.next_poll = now + self.POLL_INTERVAL
    except OSError:
      self.close()
      self.next_poll = now + 2.
    return self.limit if now < self.expires else 0.

  def apply(self, car_limit):
    headunit_limit = self.update()
    return car_limit if car_limit > 0. else headunit_limit

  def preview(self):
    now = self.clock()
    age = now - self.sample_time
    valid = self.limit > 0. and now < self.expires and self.ahead > 0. and 0 <= age <= 1.
    return (self.ahead, self.ahead_distance, True, age) if valid else (0., 0., False, 0.)

  def apply_state(self, state):
    car_limit = state.speedLimit
    state.speedLimit = self.apply(car_limit)
    ahead, distance, valid, age = self.preview() if car_limit <= 0. else (0., 0., False, 0.)
    state.speedLimitAhead = ahead
    state.speedLimitAheadDistance = distance
    state.speedLimitAheadValid = valid
    state.speedLimitAheadAge = age
