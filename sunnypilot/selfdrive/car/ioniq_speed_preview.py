"""Factory-navigation preview selection; values use metres and metres/second.

The producer must establish branch applicability before setting ahead_valid.
Raw or offline diagnostic graph candidates do not satisfy that contract.
"""

from math import isfinite
from pathlib import Path


ACCELERATION_ADVANCE_PATH = '/data/ioniq-speed-limit/acceleration_advance_m'


def load_acceleration_advance(path=ACCELERATION_ADVANCE_PATH):
  """Read metres; missing configuration defaults to 25, invalid disables it."""
  try:
    value = float(Path(path).read_text().strip())
  except FileNotFoundError:
    return 25.0
  except (OSError, ValueError, OverflowError):
    return 0.0
  return value if isfinite(value) and 0 <= value <= 50 else 0.0


class PreviewReleaseGuard:
  """A lost preview is not evidence that a higher limit has begun.

  Suppress an automatic higher recommendation until the current factory limit
  changes, the driver disengages, or the source policy is explicitly changed.
  Returning false invalidates the recommendation; it does not publish an old
  preview as a current speed limit or interfere with manual cruise buttons.
  """

  def __init__(self):
    self.base = None
    self.ceiling = None
    self.policy = None

  def allow(self, current, selected, *, car_source, policy, engaged):
    if not engaged or policy != self.policy:
      self.base = self.ceiling = None
    self.policy = policy
    if not engaged:
      return True
    valid_current = isinstance(current, (int, float)) and isfinite(current) and 5 / 3.6 <= current <= 200 / 3.6
    if self.base is not None and valid_current and abs(current - self.base) > .01:
      self.base = self.ceiling = None
    if car_source and valid_current and 0 < selected < current:
      self.base = current
      self.ceiling = selected if self.ceiling is None else min(self.ceiling, selected)
    return self.ceiling is None or selected <= self.ceiling + .01


def select_preview(current, ahead, distance, v_ego, *, ahead_valid=False,
                   age_s=None, braking=1.0, reaction_s=1.0, acceleration_advance_m=0.0):
  """Return a resolver (limit, distance) pair, preserving current on uncertainty.

  A fresh higher limit is selectable within the configured advance distance.
  Zero disables advance. This moves the target change before the map boundary;
  actual vehicle response and map boundary accuracy must be measured separately.
  """
  def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)

  if not finite(current) or not 5 / 3.6 <= current <= 200 / 3.6:
    return 0.0, 0.0
  fallback = current, 0.0
  if ahead_valid is not True:
    return fallback
  if not all(finite(value) for value in (ahead, distance, v_ego, age_s, braking, reaction_s)):
    return fallback
  if not (5 / 3.6 <= ahead <= 200 / 3.6 and 0 <= distance <= 2000
          and 0 <= v_ego <= 80 and 0 <= age_s <= 1.0
          and 0 < braking <= 1.5 and 0 <= reaction_s <= 2):
    return fallback
  remaining = max(0.0, distance - v_ego * age_s)
  if ahead > current:
    if (finite(acceleration_advance_m) and 0 < acceleration_advance_m <= 50
        and v_ego > 0 and remaining <= acceleration_advance_m):
      return ahead, 0.0
    return fallback
  if ahead == current:
    return fallback
  # Use current limit too: a car below the upcoming limit must not accelerate
  # toward the old, higher limit immediately before the restriction.
  approach_speed = max(v_ego, current)
  braking_distance = max(0.0, (approach_speed ** 2 - ahead ** 2) / (2 * braking))
  if remaining <= braking_distance + approach_speed * reaction_s:
    return ahead, remaining
  return fallback
