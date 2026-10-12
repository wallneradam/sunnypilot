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


def scheduled_messages(ctrl, command, metric=True, frames=120):
  cs = SimpleNamespace(is_metric=metric, clu11=defaultdict(int))
  cc = SimpleNamespace(intelligentCruiseButtonManagement=SimpleNamespace(sendButton=command))
  packer = CANPacker(DBC[ctrl.CP.carFingerprint][Bus.pt])
  sends = {}
  for frame in range(1, frames + 1):
    messages = ctrl.update(cs, cc, packer, frame, ctrl.last_button_frame, None)
    if messages:
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
