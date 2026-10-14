from types import SimpleNamespace

import pytest

from cereal import car, custom, log
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events, ET
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.sunnypilot.selfdrive.selfdrived.button_state_tracker import ButtonStateTracker
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import EmptyAlert, NormalPermanentAlert, Priority

ButtonType = car.CarState.ButtonEvent.Type
EventNameSP = custom.OnroadEventSP.EventName


@pytest.fixture
def controls():
  controls = SelfdriveD.__new__(SelfdriveD)
  controls.CP = car.CarParams(openpilotLongitudinalControl=False, pcmCruise=True)
  controls.sm = SimpleNamespace(frame=0)
  controls.is_metric = True
  controls.enabled = True
  controls.personality = log.LongitudinalPersonality.standard
  controls.state_machine = SimpleNamespace(current_alert_types=[ET.WARNING, ET.PERMANENT], soft_disable_timer=0)
  controls.events = Events()
  controls.events_sp = EventsSP()
  controls.speed_limit_mode_alert = None
  controls.speed_limit_alert_suppress_until = 0
  controls.button_state_tracker = ButtonStateTracker()
  controls.AM = AlertManager()
  return controls


def step(controls, frame, button=None, pressed=True):
  controls.sm.frame = frame
  cs = car.CarState()
  if button is not None:
    cs.buttonEvents = [car.CarState.ButtonEvent(type=button, pressed=pressed)]
  controls.update_alerts(cs)
  controls.button_state_tracker.update(cs)
  return controls.AM.current_alert


@pytest.mark.parametrize('button', [ButtonType.accelCruise, ButtonType.decelCruise, ButtonType.resumeCruise, ButtonType.setCruise])
@pytest.mark.parametrize('event', [EventNameSP.speedLimitActive, EventNameSP.speedLimitChanged, EventNameSP.speedLimitPending])
def test_press_clears_existing_alert_and_stale_repeated_event(controls, button, event):
  controls.events_sp.add(event)
  assert step(controls, 0) != EmptyAlert
  assert step(controls, 1, button) == EmptyAlert
  assert step(controls, 2) == EmptyAlert
  assert step(controls, 3, button, pressed=False) == EmptyAlert
  assert step(controls, 50) == EmptyAlert
  controls.events_sp.clear()
  assert step(controls, 110) == EmptyAlert
  controls.events_sp.add(event)
  assert step(controls, 111) != EmptyAlert


def test_long_hold_and_repeated_taps_keep_target_visible(controls):
  controls.events_sp.add(EventNameSP.speedLimitActive)
  assert step(controls, 0) != EmptyAlert
  assert step(controls, 1, ButtonType.accelCruise) == EmptyAlert
  for frame in range(2, 301):
    assert step(controls, frame) == EmptyAlert
  assert step(controls, 301, ButtonType.accelCruise, pressed=False) == EmptyAlert
  assert step(controls, 370, ButtonType.decelCruise) == EmptyAlert
  assert step(controls, 371, ButtonType.decelCruise, pressed=False) == EmptyAlert
  assert step(controls, 450) == EmptyAlert


def test_confirmation_prompt_is_suppressed_on_press(controls):
  resolver = SimpleNamespace(speedLimitValid=True, speedLimitFinalLast=95 / 3.6)
  controls.sm = {'longitudinalPlanSP': SimpleNamespace(speedLimit=SimpleNamespace(resolver=resolver)),
                 'controlsState': SimpleNamespace(deprecated=SimpleNamespace(vCruise=55))}

  class SubMaster(dict):
    frame = 0

  controls.sm = SubMaster(controls.sm)
  controls.events_sp.add(EventNameSP.speedLimitPreActive)
  assert step(controls, 0) != EmptyAlert
  assert step(controls, 1, ButtonType.accelCruise) == EmptyAlert


def test_other_warnings_and_mode_feedback_remain_visible(controls):
  controls.events_sp.add(EventNameSP.speedLimitActive)
  assert step(controls, 0) != EmptyAlert
  controls.events_sp.add(EventNameSP.manualLongitudinalRequired)
  assert step(controls, 1, ButtonType.decelCruise).alert_type == 'manualLongitudinalRequired/warning'
  alert = NormalPermanentAlert('Speed limits: OFF', duration=3., priority=Priority.MID)
  alert.alert_type = 'speedLimitModeChanged/permanent'
  alert.event_type = ET.PERMANENT
  controls.speed_limit_mode_alert = alert
  assert step(controls, 2).alert_type == alert.alert_type


def test_gap_button_does_not_dismiss_speed_notification(controls):
  controls.events_sp.add(EventNameSP.speedLimitActive)
  assert step(controls, 0) != EmptyAlert
  assert step(controls, 1, ButtonType.gapAdjustCruise) != EmptyAlert
