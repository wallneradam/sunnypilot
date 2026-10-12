import tempfile
from pathlib import Path
from openpilot.sunnypilot.selfdrive.car.ioniq_speed_preview import select_preview, PreviewReleaseGuard, load_acceleration_advance


class TestPreview:
  def test_lost_preview_cannot_trigger_acceleration(self):
    guard = PreviewReleaseGuard()
    args = dict(car_source=True, policy=2, engaged=True)
    assert guard.allow(25.0, 50 / 3.6, **args)
    assert not guard.allow(25.0, 25.0, **args)
    assert not guard.allow(0.0, 25.0, car_source=False, policy=2, engaged=True)
    assert guard.allow(0.0, 0.0, **args)
    assert guard.allow(50 / 3.6, 50 / 3.6, **args)
    assert guard.allow(25.0, 25.0, **args)

  def test_disengagement_and_explicit_policy_change_reset_guard(self):
    for change in (dict(policy=1, engaged=True), dict(policy=2, engaged=False)):
      guard = PreviewReleaseGuard()
      guard.allow(25.0, 50 / 3.6, car_source=True, policy=2, engaged=True)
      assert guard.allow(25.0, 25.0, car_source=True, **change)

  def select(self, **changes):
    values = dict(current=90 / 3.6, ahead=50 / 3.6, distance=200, v_ego=90 / 3.6, ahead_valid=True, age_s=0)
    values.update(changes)
    return select_preview(**values)

  def test_lower_limit_with_distance(self):
    assert self.select() == (50 / 3.6, 200)
    assert self.select(distance=500) == (90 / 3.6, 0)
    assert self.select(age_s=0.5) == (50 / 3.6, 187.5)

  def test_higher_limit_waits_for_current_source_transition(self):
    for distance in (100, 10, 1, 0):
      assert self.select(current=50 / 3.6, ahead=90 / 3.6, distance=distance) == (50 / 3.6, 0)
    assert self.select(current=90 / 3.6, ahead=90 / 3.6, distance=0) == (90 / 3.6, 0)

  def test_does_not_accelerate_toward_old_limit_near_lower_sign(self):
    assert self.select(v_ego=40 / 3.6, distance=10) == (50 / 3.6, 10)

  def test_acceleration_advance_threshold_and_age(self):
    args = dict(current=50 / 3.6, ahead=60 / 3.6, v_ego=50 / 3.6, acceleration_advance_m=25)
    assert self.select(distance=25.01, **args) == (50 / 3.6, 0)
    assert self.select(distance=25, **args) == (60 / 3.6, 0)
    assert self.select(distance=30, age_s=0.5, **args) == (60 / 3.6, 0)
    assert self.select(distance=20, age_s=1.01, **args) == (50 / 3.6, 0)
    assert self.select(distance=20, ahead_valid=False, **args) == (50 / 3.6, 0)
    assert self.select(distance=20, current=0, ahead=60 / 3.6, acceleration_advance_m=25) == (0, 0)

  def test_disabled_invalid_or_stationary_advance(self):
    for advance in (0, -1, 200.01, float('nan'), float('inf'), True, None):
      assert self.select(current=50 / 3.6, ahead=60 / 3.6, distance=0, acceleration_advance_m=advance) == (50 / 3.6, 0)
    assert self.select(current=50 / 3.6, ahead=60 / 3.6, distance=0, v_ego=0, acceleration_advance_m=25) == (50 / 3.6, 0)

  def test_advance_configuration(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'advance'
      assert load_acceleration_advance(path) == 25
      for value, expected in (
        ('25', 25),
        ('20.5', 20.5),
        ('0', 0),
        ('50', 50),
        ('51', 51),
        ('150', 150),
        ('200', 200),
        ('200.01', 0),
        ('-1', 0),
        ('nan', 0),
        ('inf', 0),
        ('', 0),
        ('oops', 0),
      ):
        path.write_text(value)
        assert load_acceleration_advance(path) == expected

  def test_configured_150_m_advance_is_used_by_selection(self):
    args = dict(current=50 / 3.6, ahead=90 / 3.6, v_ego=50 / 3.6, acceleration_advance_m=150)
    assert self.select(distance=150.01, **args) == (50 / 3.6, 0)
    assert self.select(distance=150, **args) == (90 / 3.6, 0)
    assert self.select(distance=25, **args) == (90 / 3.6, 0)
    assert self.select(distance=155, age_s=0.5, **args) == (90 / 3.6, 0)
    for changes in (dict(ahead_valid=False), dict(age_s=1.01), dict(v_ego=0)):
      assert self.select(distance=25, **args | changes) == (50 / 3.6, 0)
    assert self.select(distance=100, acceleration_advance_m=150) == (50 / 3.6, 100)

  def test_every_accepted_positive_setting_is_selectable(self):
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / 'advance'
      for metres in range(1, 201):
        path.write_text(str(metres))
        advance = load_acceleration_advance(path)
        assert advance == metres
        args = dict(current=50 / 3.6, ahead=90 / 3.6, acceleration_advance_m=advance, v_ego=50 / 3.6)
        assert self.select(distance=metres, **args) == (90 / 3.6, 0)
        assert self.select(distance=metres + 0.01, **args) == (50 / 3.6, 0)

  def test_invalid_current_never_uses_preview(self):
    for current in (0, -1, float('nan'), float('inf'), True):
      assert self.select(current=current) == (0, 0)

  def test_untrusted_expired_or_malformed_preview_keeps_current(self):
    for changes in (
      dict(ahead_valid=False),
      dict(ahead_valid=1),
      dict(age_s=1.01),
      dict(age_s=-0.1),
      dict(age_s=None),
      dict(distance=-1),
      dict(distance=float('nan')),
      dict(ahead=float('inf')),
      dict(ahead=0),
      dict(braking=0),
      dict(v_ego=-1),
    ):
      assert self.select(**changes) == (90 / 3.6, 0)
