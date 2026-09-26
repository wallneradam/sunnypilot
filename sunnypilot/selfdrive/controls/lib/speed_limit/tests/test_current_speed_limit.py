from types import SimpleNamespace

import pytest

from cereal import custom, car
from openpilot.common.params import Params
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import speed_limit_assist as sla_module
from openpilot.sunnypilot.selfdrive.selfdrived import events
from openpilot.sunnypilot.selfdrive.car.cruise_ext import VCruiseHelperSP

State = custom.LongitudinalPlanSP.SpeedLimit.AssistState


@pytest.mark.parametrize('metric,set_speed,target,expected', [
  (True, 55, 95 / 3.6, '95 km/h · Press + to confirm'),
  (True, 95, 55 / 3.6, '55 km/h · Press - to confirm'),
  (False, 80.4672, 60 * 0.44704, '60 mph · Press + to confirm'),
  (False, 112.65408, 60 * 0.44704, '60 mph · Press - to confirm'),
])
def test_confirmation_text(monkeypatch, metric, set_speed, target, expected):
  monkeypatch.setattr(events, 'IS_MICI', True)
  resolver = SimpleNamespace(speedLimitValid=True, speedLimitFinalLast=target)
  sm = {'longitudinalPlanSP': SimpleNamespace(speedLimit=SimpleNamespace(resolver=resolver)),
        'controlsState': SimpleNamespace(deprecated=SimpleNamespace(vCruise=set_speed))}
  cp = SimpleNamespace(openpilotLongitudinalControl=False, pcmCruise=True)
  for cluster in (set_speed, 0.):
    alert = events.speed_limit_pre_active_alert(cp, SimpleNamespace(vCruiseCluster=cluster), sm, metric, 0, None)
    assert alert.alert_text_1 == expected
  resolver.speedLimitValid = False
  alert = events.speed_limit_pre_active_alert(cp, SimpleNamespace(vCruiseCluster=set_speed), sm, metric, 0, None)
  assert alert.alert_text_1 == ''
  assert alert.alert_size == events.AlertSize.none
  assert alert.audible_alert == events.AudibleAlert.none


@pytest.mark.parametrize('pcm_long', [False, True])
@pytest.mark.parametrize('initial', [State.disabled, State.preActive, State.active, State.adapting, State.pending, State.inactive])
def test_lost_limit_never_reuses_95_and_recovers(monkeypatch, pcm_long, initial):
  params = Params()
  params.put('SpeedLimitMode', 3, block=True)
  params.put_bool('IsMetric', True, block=True)
  monkeypatch.setattr(sla_module, 'set_speed_limit_assist_availability', lambda *args: None)
  sla = sla_module.SpeedLimitAssist(SimpleNamespace(openpilotLongitudinalControl=pcm_long, pcmCruise=True), None)
  sla.state = initial
  sla._plus_hold = 1e20
  for _ in range(30):
    ev = events.EventsSP()
    sla.update(True, False, 38 / 3.6, 0., 38 / 3.6, 0., 95 / 3.6, True, 0., ev)
    assert not sla.is_enabled and not sla.is_active
    assert sla.output_v_target == sla_module.V_CRUISE_UNSET
    assert not ev.to_msg()
  assert sla._plus_hold == 0.
  sla.update(True, False, 38 / 3.6, 0., 38 / 3.6, 50 / 3.6, 55 / 3.6, True, 0., events.EventsSP())
  assert sla.state == State.preActive


@pytest.mark.parametrize('state', [State.preActive, State.active])
def test_stale_plan_cannot_consume_button_or_change_cruise(state):
  helper = VCruiseHelperSP.__new__(VCruiseHelperSP)
  helper.v_cruise_kph = helper.v_cruise_cluster_kph = 38.
  helper.v_cruise_min = 30.
  helper.prev_sla_state = State.preActive
  helper.prev_speed_limit_final_last_kph = 0.
  resolver = SimpleNamespace(speedLimitValid=False, speedLimitLastValid=True, speedLimitFinalLast=95 / 3.6)
  plan = SimpleNamespace(speedLimit=SimpleNamespace(resolver=resolver, assist=SimpleNamespace(state=state)))
  helper.update_speed_limit_assist(True, plan)
  assert not helper.update_speed_limit_assist_pre_active_confirmed(car.CarState.ButtonEvent.Type.accelCruise)
  helper.update_speed_limit_assist_v_cruise_non_pcm()
  assert helper.v_cruise_kph == 38.
