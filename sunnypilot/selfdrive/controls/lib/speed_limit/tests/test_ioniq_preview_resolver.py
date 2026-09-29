"""Factory-navigation preview selection through the real resolver."""

import time
from types import SimpleNamespace
import unittest

from cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Policy

from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import speed_limit_resolver as staged

class IntegrationTests(unittest.TestCase):
  def setUp(self):
    self.resolver = staged.SpeedLimitResolver()
    self.resolver.v_ego = 25.
    self.state = SimpleNamespace(speedLimit=25., speedLimitAhead=50 / 3.6,
                                 speedLimitAheadDistance=100., speedLimitAheadValid=True,
                                 speedLimitAheadAge=0.)
    self.sm = {'carStateSP': self.state,
               self.resolver._gps_location_service: SimpleNamespace(unixTimestampMillis=time.monotonic() * 1000),
               'liveMapDataSP': SimpleNamespace(speedLimit=20., speedLimitValid=True,
                                               speedLimitAhead=0., speedLimitAheadValid=False,
                                               speedLimitAheadDistance=0.)}

  def test_existing_policies_preserve_car_map_choice(self):
    source = custom.LongitudinalPlanSP.SpeedLimit.Source
    for policy, limit, distance, expected_source in (
        (Policy.car_state_only, 50 / 3.6, 100, source.car),
        (Policy.map_data_only, 20, 0, source.map),
        (Policy.car_state_priority, 50 / 3.6, 100, source.car),
        (Policy.map_data_priority, 20, 0, source.map),
        (Policy.combined, 50 / 3.6, 100, source.car)):
      with self.subTest(policy=policy):
        self.resolver.policy = policy
        self.assertEqual(self.resolver._resolve_limit_sources(self.sm), (limit, distance, expected_source))

  def test_current_schema_without_preview_is_compatible(self):
    self.sm['carStateSP'] = custom.CarStateSP.new_message(speedLimit=25.)
    self.resolver.policy = Policy.car_state_only
    self.assertEqual(self.resolver._resolve_limit_sources(self.sm)[:2], (25., 0.))

  def test_stale_preview_falls_back_to_current(self):
    self.state.speedLimitAheadAge = 2.
    self.resolver.policy = Policy.car_state_only
    self.assertEqual(self.resolver._resolve_limit_sources(self.sm)[:2], (25., 0.))

  def test_no_current_limit_falls_back_to_map(self):
    self.state.speedLimit = 0.
    self.resolver.policy = Policy.car_state_priority
    self.assertEqual(self.resolver._resolve_limit_sources(self.sm)[:2], (20., 0.))

  def test_higher_limit_advance_and_disable(self):
    self.state.speedLimit = 50 / 3.6
    self.state.speedLimitAhead = 90 / 3.6
    self.state.speedLimitAheadDistance = 25.
    self.resolver.policy = Policy.car_state_only
    self.resolver.acceleration_advance_m = 25.
    self.assertEqual(self.resolver._resolve_limit_sources(self.sm)[:2], (90 / 3.6, 0.))
    self.state.speedLimitAheadDistance = 25.01
    self.assertEqual(self.resolver._resolve_limit_sources(self.sm)[:2], (50 / 3.6, 0.))
    self.state.speedLimitAheadDistance = 0.
    self.resolver.acceleration_advance_m = 0.
    self.assertEqual(self.resolver._resolve_limit_sources(self.sm)[:2], (50 / 3.6, 0.))

  def test_preview_loss_disables_higher_recommendation_until_current_transition(self):
    self.sm['carControl'] = SimpleNamespace(enabled=True)
    self.resolver.policy = Policy.car_state_priority
    self.resolver.frame = 1
    self.resolver.offset_type = 0
    self.resolver.update(25., self.sm)
    self.assertAlmostEqual(self.resolver.speed_limit, 50 / 3.6)
    self.state.speedLimitAheadValid = False
    self.resolver.update(25., self.sm)
    self.assertFalse(self.resolver.speed_limit_valid)
    self.state.speedLimit = 0.
    self.resolver.update(25., self.sm)
    self.assertFalse(self.resolver.speed_limit_valid)
    self.state.speedLimit = 50 / 3.6
    self.resolver.update(25., self.sm)
    self.assertTrue(self.resolver.speed_limit_valid)
    self.assertAlmostEqual(self.resolver.speed_limit, 50 / 3.6)


if __name__ == '__main__':
  unittest.main()
