from types import SimpleNamespace
import time

import pytest

from cereal import car
from openpilot.common.params import Params
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.mode_selector import SpeedLimitModeSelector
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import speed_limit_assist as sla_module
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP


class MemoryParams:
  def __init__(self, mode):
    self.mode = mode

  def get(self, *args, **kwargs):
    return self.mode

  def put(self, key, value, **kwargs):
    assert key == 'SpeedLimitMode'
    self.mode = value


def state(pressed=None, valid=True, button=car.CarState.ButtonEvent.Type.accelCruise):
  buttons = [] if pressed is None else [SimpleNamespace(type=button, pressed=pressed)]
  return SimpleNamespace(buttonEvents=buttons, canValid=valid, canTimeout=False,
                         cruiseState=SimpleNamespace(enabled=True), vEgo=15.)


def test_short_press_does_not_switch():
  params = MemoryParams(Mode.assist)
  selector = SpeedLimitModeSelector(params)
  assert selector.update(state(True)) is None
  for _ in range(40):
    assert selector.update(state()) is None
  assert selector.update(state(False)) is None
  assert params.mode == Mode.assist


def test_hold_cycles_once_until_release():
  params = MemoryParams(Mode.off)
  selector = SpeedLimitModeSelector(params)
  for expected, label in ((Mode.assist, 'CONFIRM'), (Mode.auto, 'AUTO'), (Mode.assist, 'CONFIRM')):
    alerts = []
    for n in range(400):
      alert = selector.update(state(True if n == 0 else None))
      if alert is not None:
        alerts.append(alert)
    assert params.mode == expected
    assert len(alerts) == 1
    assert alerts[0].alert_text_1 == f'Speed limits: {label}'
    assert selector.update(state(False)) is None
    assert car.CarState.ButtonEvent.Type.accelCruise in selector.suppressed_releases


@pytest.mark.parametrize('mode', [Mode.off, Mode.assist, Mode.auto])
def test_long_minus_disables(mode):
  params = MemoryParams(mode)
  selector = SpeedLimitModeSelector(params)
  selector.update(state(True, button=car.CarState.ButtonEvent.Type.decelCruise))
  for _ in range(110):
    selector.update(state())
  assert params.mode == Mode.off


def test_gap_no_longer_switches():
  params = MemoryParams(Mode.assist)
  selector = SpeedLimitModeSelector(params)
  selector.update(state(True, button=car.CarState.ButtonEvent.Type.gapAdjustCruise))
  for _ in range(200):
    assert selector.update(state()) is None
  assert params.mode == Mode.assist


@pytest.mark.parametrize('button', [car.CarState.ButtonEvent.Type.accelCruise, car.CarState.ButtonEvent.Type.decelCruise])
@pytest.mark.parametrize('frames', [1, 49, 50, 98, 99, 100, 101, 400])
def test_hold_does_not_adjust_or_confirm(button, frames):
  from cereal import custom
  from openpilot.selfdrive.car.cruise import VCruiseHelper
  from openpilot.sunnypilot.selfdrive.selfdrived.button_state_tracker import ButtonStateTracker

  helper = VCruiseHelper(car.CarParams(brand='hyundai', pcmCruise=True), custom.CarParamsSP(pcmCruiseSpeed=False))
  helper.v_cruise_kph = 80
  helper.v_cruise_cluster_kph = 80
  helper.enabled_prev = True
  helper.custom_acc_enabled = True
  helper.short_increment = 5
  selector = SpeedLimitModeSelector(MemoryParams(Mode.assist))
  tracker = ButtonStateTracker()
  for n in range(frames + 1):
    cs = car.CarState(canValid=True, cruiseState={'available': True, 'enabled': True})
    if n in (0, frames):
      cs.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=n == 0)]
    selector.update(cs)
    tracker.update(cs, selector.suppressed_releases)
    helper.update_v_cruise(cs, True, True)
    if n < frames or frames >= 100:
      assert helper.v_cruise_kph == 80
  if frames >= 100:
    assert tracker.release_toggle == 0
  else:
    assert helper.v_cruise_kph == (85 if button == car.CarState.ButtonEvent.Type.accelCruise else 75)
    assert tracker.release_toggle == 1 << button


def test_invalid_can_cannot_complete_hold():
  params = MemoryParams(Mode.assist)
  selector = SpeedLimitModeSelector(params)
  selector.update(state(True))
  for _ in range(40):
    selector.update(state())
  selector.update(state(valid=False))
  for _ in range(200):
    assert selector.update(state(True)) is None
  assert params.mode == Mode.assist
  selector.update(state(False))
  selector.update(state(True))
  for _ in range(100):
    selector.update(state())
  assert params.mode == Mode.auto


def test_selector_persists_real_parameter():
  params = Params()
  params.put('SpeedLimitMode', int(Mode.assist), block=True)
  selector = SpeedLimitModeSelector(params)
  selector.update(state(True))
  for _ in range(99):
    selector.update(state())
  deadline = time.monotonic() + 1.
  while params.get('SpeedLimitMode') != int(Mode.auto) and time.monotonic() < deadline:
    time.sleep(.01)
  assert params.get('SpeedLimitMode') == int(Mode.auto)


@pytest.fixture
def sla(monkeypatch):
  params = Params()
  params.put('SpeedLimitMode', int(Mode.auto), block=True)
  params.put_bool('IsMetric', True, block=True)
  monkeypatch.setattr(sla_module, 'set_speed_limit_assist_availability', lambda *args: None)
  return sla_module.SpeedLimitAssist(SimpleNamespace(openpilotLongitudinalControl=False, pcmCruise=True), None)


def advance(sla, set_speed, limit, count=20, enabled=True):
  events = EventsSP()
  for _ in range(count):
    events = EventsSP()
    sla.update(enabled, False, 50 / 3.6, 0., set_speed / 3.6, limit / 3.6,
               (limit + 5) / 3.6 if limit else 95 / 3.6, limit > 0, 0., events)
  return events


def test_auto_accepts_and_manual_override_waits_for_new_limit(sla):
  advance(sla, 55, 90)
  assert sla.is_active
  assert sla.output_v_target == pytest.approx(95 / 3.6)
  advance(sla, 95, 90)
  assert sla.is_active
  advance(sla, 100, 90)
  assert not sla.is_active
  assert sla.auto_override
  assert sla.output_v_target == sla_module.V_CRUISE_UNSET
  advance(sla, 100, 50)
  assert sla.is_active
  assert sla.output_v_target == pytest.approx(55 / 3.6)


def test_auto_data_loss_and_mode_off(sla):
  advance(sla, 55, 90)
  assert sla.is_active
  events = advance(sla, 95, 0)
  assert not sla.is_active
  assert not events.to_msg()
  assert sla.output_v_target == sla_module.V_CRUISE_UNSET
  advance(sla, 95, 50)
  assert sla.is_active
  Params().put('SpeedLimitMode', int(Mode.off), block=True)
  events = advance(sla, 55, 50, count=1)
  assert not sla.is_active
  assert not events.to_msg()
  assert sla.output_v_target == sla_module.V_CRUISE_UNSET


def test_confirm_after_auto_requires_confirmation(sla):
  advance(sla, 55, 90)
  Params().put('SpeedLimitMode', int(Mode.assist), block=True)
  advance(sla, 55, 90)
  assert sla.state == sla_module.SpeedLimitAssistState.preActive
  assert not sla.is_active


def test_auto_never_activates_with_cruise_disabled(sla):
  advance(sla, 55, 90, enabled=False)
  assert not sla.is_active
  assert sla.output_v_target == sla_module.V_CRUISE_UNSET
