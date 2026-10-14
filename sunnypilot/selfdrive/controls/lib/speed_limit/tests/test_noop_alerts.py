from types import SimpleNamespace

import pytest

from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.prefix import OpenpilotPrefix
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import speed_limit_assist as sla_module
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP


@pytest.fixture
def sla(monkeypatch):
  with OpenpilotPrefix():
    params = Params()
    params.put('SpeedLimitMode', int(Mode.auto), block=True)
    params.put_bool('IsMetric', True, block=True)
    monkeypatch.setattr(sla_module, 'set_speed_limit_assist_availability', lambda *args: None)
    yield sla_module.SpeedLimitAssist(SimpleNamespace(openpilotLongitudinalControl=False, pcmCruise=True), None)


def update(sla, cruise, limit, target, valid=True):
  events = EventsSP()
  sla.update(True, False, 50 / 3.6, 0., cruise, limit, target, valid, 0., events)
  return events.names


@pytest.mark.parametrize('metric,speed_conv', [(True, CV.MS_TO_KPH), (False, CV.MS_TO_MPH)])
def test_activation_and_recovery_at_current_set_speed_are_silent(sla, metric, speed_conv):
  Params().put_bool('IsMetric', metric, block=True)
  sla.is_metric = metric
  target = 95 / speed_conv
  for _ in range(20):
    assert update(sla, target, 90 / speed_conv, target) == []
  assert sla.is_active
  assert sla.output_v_target == pytest.approx(target)
  assert update(sla, target, 0., target, valid=False) == []
  assert update(sla, target, 90 / speed_conv, target) == []
  assert sla.is_active


def test_changed_raw_limit_with_same_displayed_target_is_silent(sla):
  for _ in range(20):
    update(sla, 55 / 3.6, 90 / 3.6, 95 / 3.6)
  assert sla.is_active
  assert update(sla, 55 / 3.6, 90.01 / 3.6, 95.01 / 3.6) == []
  assert sla.output_v_target == pytest.approx(95.01 / 3.6)


def test_real_target_changes_still_notify_and_control(sla):
  seen = []
  for _ in range(20):
    seen.extend(update(sla, 55 / 3.6, 90 / 3.6, 95 / 3.6))
  assert len(seen) == 1
  assert sla.output_v_target == pytest.approx(95 / 3.6)
  for _ in range(20):
    assert update(sla, 95 / 3.6, 90 / 3.6, 95 / 3.6) == []
  assert len(update(sla, 95 / 3.6, 50 / 3.6, 55 / 3.6)) == 1
  assert sla.output_v_target == pytest.approx(55 / 3.6)


def test_offset_change_notifies_using_effective_target(sla):
  for _ in range(20):
    update(sla, 95 / 3.6, 90 / 3.6, 95 / 3.6)
  assert len(update(sla, 95 / 3.6, 90 / 3.6, 100 / 3.6)) == 1
  assert sla.output_v_target == pytest.approx(100 / 3.6)


def test_pcm_long_still_notifies_when_limit_controls_acceleration(sla):
  sla.pcm_op_long = True
  sla.v_cruise_cluster_conv = sla.target_set_speed_conv = 145
  events = EventsSP()
  sla.update_active_event(events)
  assert len(events.names) == 1
