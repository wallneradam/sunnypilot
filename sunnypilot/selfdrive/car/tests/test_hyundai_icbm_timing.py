from collections import defaultdict
from types import SimpleNamespace

import pytest
from cereal import car

from opendbc.can.packer import CANPacker
from opendbc.car.hyundai import hyundaican
from opendbc.car.hyundai.values import Buttons, CAR, DBC
from opendbc.car import Bus, structs
from opendbc.sunnypilot.car.hyundai.icbm import IntelligentCruiseButtonManagementInterface
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.controller import (
  IntelligentCruiseButtonManagement, SendButtonState as ControllerSendButtonState, State,
)

SendButtonState = structs.IntelligentCruiseButtonManagement.SendButtonState


def interface(car=CAR.HYUNDAI_IONIQ_EV_LTD):
  cp = SimpleNamespace(carFingerprint=car, flags=0)
  return IntelligentCruiseButtonManagementInterface(cp, SimpleNamespace())


def car_state(metric=True):
  state = structs.CarState()
  state.canValid = True
  state.canTimeout = False
  state.cruiseState.enabled = True
  state.cruiseState.speedCluster = 50 / 3.6
  return SimpleNamespace(is_metric=metric, clu11=defaultdict(int), out=state)


def scheduled_messages(ctrl, command, metric=True, frames=120):
  cs = car_state(metric)
  acknowledge = None
  last_sent = None
  cc = SimpleNamespace(intelligentCruiseButtonManagement=SimpleNamespace(sendButton=command))
  packer = CANPacker(DBC[ctrl.CP.carFingerprint][Bus.pt])
  sends = {}
  for frame in range(1, frames + 1):
    if frame == acknowledge:
      cs.out.cruiseState.speedCluster += (1 if command == SendButtonState.increase else -1) / 3.6
      acknowledge = None
    messages = ctrl.update(cs, cc, packer, frame, ctrl.last_button_frame, None)
    if messages:
      if last_sent is None or frame - last_sent > 1:
        acknowledge = frame + 9
      last_sent = frame
      sends[frame] = messages
  return sends, cs, packer


@pytest.mark.parametrize('command,button', [(SendButtonState.increase, Buttons.RES_ACCEL), (SendButtonState.decrease, Buttons.SET_DECEL)])
@pytest.mark.parametrize('metric', [True, False])
def test_ioniq_shorter_cycles_keep_five_frames_and_packet_contents(command, button, metric):
  ctrl = interface()
  sends, cs, packer = scheduled_messages(ctrl, command, metric)
  expected = [frame for start in range(8, 121, 12) for frame in range(start, start + 5)]
  assert list(sends) == expected
  assert len(sends) == 50
  for frame, messages in sends.items():
    assert messages == [hyundaican.create_clu11(packer, frame, cs.clu11, button, ctrl.CP)]


@pytest.mark.parametrize('command', [SendButtonState.increase, SendButtonState.decrease])
@pytest.mark.parametrize('metric', [True, False])
def test_other_hyundai_keeps_original_schedule(command, metric):
  sends, _, _ = scheduled_messages(interface(CAR.HYUNDAI_SONATA), command, metric)
  expected = [frame for start in range(11, 121, 15) for frame in range(start, start + 5)]
  assert list(sends) == expected
  assert len(sends) == 40


def test_no_requested_button_sends_no_messages():
  sends, _, _ = scheduled_messages(interface(), SendButtonState.none)
  assert sends == {}


def test_cycle_uses_the_last_button_frame():
  ctrl = interface()
  ctrl.last_button_frame = 73
  sends, _, _ = scheduled_messages(ctrl, SendButtonState.increase, frames=85)
  assert list(sends) == [81, 82, 83, 84, 85]


@pytest.mark.parametrize('state,cluster,target', [(State.increasing, 70, 70), (State.decreasing, 50, 50)])
def test_target_reached_stops_button_requests(state, cluster, target):
  ctrl = IntelligentCruiseButtonManagement(SimpleNamespace(), SimpleNamespace(pcmCruiseSpeed=False))
  ctrl.state = state
  ctrl.is_ready = ctrl.is_ready_prev = True
  ctrl.v_target = target
  ctrl.v_cruise_cluster = cluster
  ctrl.v_cruise_min = 30
  assert ctrl.update_state_machine() == ControllerSendButtonState.none
  sends, _, _ = scheduled_messages(interface(), SendButtonState.none)
  assert sends == {}


@pytest.mark.parametrize('flag', ['override', 'cancel', 'resume'])
def test_manual_override_and_cruise_commands_stop_automatic_requests(flag):
  ctrl = IntelligentCruiseButtonManagement(SimpleNamespace(), SimpleNamespace(pcmCruiseSpeed=False))
  ctrl.cruise_button_timers = ctrl.cruise_button_timers.copy()
  ctrl.state = State.increasing
  ctrl.is_ready = ctrl.is_ready_prev = True
  cs = SimpleNamespace(buttonEvents=[], cruiseState=SimpleNamespace(speedCluster=50 / 3.6))
  cruise = SimpleNamespace(override=False, cancel=False, resume=False)
  setattr(cruise, flag, True)
  ctrl.run(cs, SimpleNamespace(enabled=True, cruiseControl=cruise), SimpleNamespace(vTarget=70 / 3.6), True)
  assert ctrl.cruise_button == ControllerSendButtonState.none


def test_disengagement_stops_automatic_requests():
  ctrl = IntelligentCruiseButtonManagement(SimpleNamespace(), SimpleNamespace(pcmCruiseSpeed=False))
  ctrl.cruise_button_timers = ctrl.cruise_button_timers.copy()
  ctrl.state = State.decreasing
  ctrl.is_ready = ctrl.is_ready_prev = True
  cs = SimpleNamespace(buttonEvents=[], cruiseState=SimpleNamespace(speedCluster=70 / 3.6))
  cruise = SimpleNamespace(override=False, cancel=False, resume=False)
  ctrl.run(cs, SimpleNamespace(enabled=False, cruiseControl=cruise), SimpleNamespace(vTarget=50 / 3.6), True)
  assert ctrl.cruise_button == ControllerSendButtonState.none


@pytest.mark.parametrize('button', ['accelCruise', 'decelCruise'])
def test_driver_button_press_stops_automatic_requests(button):
  ctrl = IntelligentCruiseButtonManagement(SimpleNamespace(), SimpleNamespace(pcmCruiseSpeed=False))
  ctrl.cruise_button_timers = ctrl.cruise_button_timers.copy()
  ctrl.state = State.increasing
  ctrl.is_ready = ctrl.is_ready_prev = True
  cs = car.CarState.new_message(buttonEvents=[{'type': button, 'pressed': True}])
  cs.cruiseState.speedCluster = 50 / 3.6
  cruise = SimpleNamespace(override=False, cancel=False, resume=False)
  ctrl.run(cs, SimpleNamespace(enabled=True, cruiseControl=cruise), SimpleNamespace(vTarget=70 / 3.6), True)
  assert ctrl.cruise_button == ControllerSendButtonState.none


def simulate_feedback(command=SendButtonState.increase, delays=(9,), missed=(), frames=180):
  ctrl = interface()
  cs = car_state()
  cc = SimpleNamespace(intelligentCruiseButtonManagement=SimpleNamespace(sendButton=command))
  packer = CANPacker(DBC[CAR.HYUNDAI_IONIQ_EV_LTD][Bus.pt])
  starts, acknowledgements, sent = [], [], []
  pending = []
  last_sent = None
  for frame in range(1, frames + 1):
    for due in pending.copy():
      if due == frame:
        cs.out.cruiseState.speedCluster += (1 if command == SendButtonState.increase else -1) / 3.6
        acknowledgements.append(frame)
        pending.remove(due)
    messages = ctrl.update(cs, cc, packer, frame, ctrl.last_button_frame, None)
    if messages:
      if last_sent is None or frame - last_sent > 1:
        starts.append(frame)
        attempt = len(starts) - 1
        if attempt not in missed:
          pending.append(frame + delays[attempt % len(delays)])
      last_sent = frame
      sent.append(frame)
  return starts, acknowledgements, sent


@pytest.mark.parametrize('command', [SendButtonState.increase, SendButtonState.decrease])
def test_accepted_steps_keep_the_120_ms_pace(command):
  starts, responses, _ = simulate_feedback(command)
  assert len(responses) >= 10
  assert all(b - a == 12 for a, b in zip(starts, starts[1:], strict=False))
  assert all(b - a == 12 for a, b in zip(responses, responses[1:], strict=False))


@pytest.mark.parametrize('command', [SendButtonState.increase, SendButtonState.decrease])
def test_missed_step_is_retried_sooner_without_a_catchup_burst(command):
  starts, responses, _ = simulate_feedback(command, missed=(2,))
  assert starts[3] - starts[2] == 11
  intervals = [b - a for a, b in zip(responses, responses[1:], strict=False)]
  assert max(intervals) == 23
  assert min(intervals) == 12
  assert all(delta == 12 for delta in intervals[2:])


@pytest.mark.parametrize('delays', [(7, 11), (11, 7), (7, 9, 11)])
def test_feedback_pacing_reduces_response_interval_variation(delays):
  _, responses, _ = simulate_feedback(delays=delays, frames=240)
  intervals = [b - a for a, b in zip(responses, responses[1:], strict=False)]
  fixed_intervals = [12 + delays[n % len(delays)] - delays[(n - 1) % len(delays)] for n in range(1, 20)]
  assert max(intervals) - min(intervals) < max(fixed_intervals) - min(fixed_intervals)
  assert all(10 <= delta <= 14 for delta in intervals)


def test_early_feedback_ends_the_press_and_does_not_exceed_ten_hz():
  starts, _, sent = simulate_feedback(delays=(2,))
  assert sent[:2] == [8, 9]
  assert 10 not in sent
  assert all(b - a >= 10 for a, b in zip(starts, starts[1:], strict=False))


def test_a_request_after_long_idle_sends_a_complete_short_press():
  ctrl = interface()
  cs = car_state()
  cc = SimpleNamespace(intelligentCruiseButtonManagement=SimpleNamespace(sendButton=SendButtonState.increase))
  packer = CANPacker(DBC[CAR.HYUNDAI_IONIQ_EV_LTD][Bus.pt])
  sent = [frame for frame in range(1000, 1006) if ctrl.update(cs, cc, packer, frame, ctrl.last_button_frame, None)]
  assert sent == [1000, 1001, 1002, 1003, 1004]


@pytest.mark.parametrize('invalid', ['canValid', 'canTimeout', 'cruiseDisabled', 'nan', 'negative', 'manualButton'])
def test_bad_feedback_or_driver_button_suppresses_requests(invalid):
  ctrl = interface()
  cs = car_state()
  if invalid == 'canValid':
    cs.out.canValid = False
  elif invalid == 'canTimeout':
    cs.out.canTimeout = True
  elif invalid == 'cruiseDisabled':
    cs.out.cruiseState.enabled = False
  elif invalid == 'nan':
    cs.out.cruiseState.speedCluster = float('nan')
  elif invalid == 'negative':
    cs.out.cruiseState.speedCluster = -1
  else:
    cs.out.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.accelCruise, pressed=True)]
  cc = SimpleNamespace(intelligentCruiseButtonManagement=SimpleNamespace(sendButton=SendButtonState.increase))
  packer = CANPacker(DBC[CAR.HYUNDAI_IONIQ_EV_LTD][Bus.pt])
  assert not ctrl.update(cs, cc, packer, 1000, 0, None)


def test_cancelling_mid_press_clears_pending_requests():
  ctrl = interface()
  cs = car_state()
  cc = SimpleNamespace(intelligentCruiseButtonManagement=SimpleNamespace(sendButton=SendButtonState.increase))
  packer = CANPacker(DBC[CAR.HYUNDAI_IONIQ_EV_LTD][Bus.pt])
  assert ctrl.update(cs, cc, packer, 1000, 0, None)
  cc.intelligentCruiseButtonManagement.sendButton = SendButtonState.none
  for frame in range(1001, 1020):
    assert not ctrl.update(cs, cc, packer, frame, ctrl.last_button_frame, None)
  assert ctrl._ioniq_press_frame is None


def test_direction_change_leaves_a_release_gap():
  ctrl = interface()
  cs = car_state()
  cc = SimpleNamespace(intelligentCruiseButtonManagement=SimpleNamespace(sendButton=SendButtonState.increase))
  packer = CANPacker(DBC[CAR.HYUNDAI_IONIQ_EV_LTD][Bus.pt])
  assert ctrl.update(cs, cc, packer, 1000, 0, None)
  cc.intelligentCruiseButtonManagement.sendButton = SendButtonState.decrease
  for frame in range(1001, 1008):
    assert not ctrl.update(cs, cc, packer, frame, ctrl.last_button_frame, None)
  assert ctrl.update(cs, cc, packer, 1008, ctrl.last_button_frame, None)


def test_opposite_speed_change_pauses_the_pending_press():
  ctrl = interface()
  cs = car_state()
  cc = SimpleNamespace(intelligentCruiseButtonManagement=SimpleNamespace(sendButton=SendButtonState.increase))
  packer = CANPacker(DBC[CAR.HYUNDAI_IONIQ_EV_LTD][Bus.pt])
  assert ctrl.update(cs, cc, packer, 1000, 0, None)
  cs.out.cruiseState.speedCluster -= 1 / 3.6
  for frame in range(1001, 1009):
    assert not ctrl.update(cs, cc, packer, frame, ctrl.last_button_frame, None)
