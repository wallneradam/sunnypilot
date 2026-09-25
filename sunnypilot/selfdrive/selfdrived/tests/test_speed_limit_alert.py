from types import SimpleNamespace

import pytest

from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.selfdrived import events


@pytest.mark.parametrize("metric, cluster_kph, fallback_kph, limit_kph, expected", [
  (True, 50, 0, 70, "Press + to confirm speed limit"),
  (True, 90, 0, 70, "Press - to confirm speed limit"),
  (False, 50, 0, 70, "Press + to confirm speed limit"),
  (False, 90, 0, 70, "Press - to confirm speed limit"),
  (True, 0, 50, 70, "Press + to confirm speed limit"),
  (True, 70, 0, 70, ""),
])
def test_speed_limit_confirmation_direction(monkeypatch, metric, cluster_kph, fallback_kph, limit_kph, expected):
  monkeypatch.setattr(events, "IS_MICI", True)
  cp = SimpleNamespace(openpilotLongitudinalControl=False, pcmCruise=True)
  cs = SimpleNamespace(vCruiseCluster=cluster_kph)
  sm = {
    "controlsState": SimpleNamespace(deprecated=SimpleNamespace(vCruise=fallback_kph)),
    "longitudinalPlanSP": SimpleNamespace(speedLimit=SimpleNamespace(
      resolver=SimpleNamespace(speedLimitFinalLast=limit_kph * CV.KPH_TO_MS))),
  }

  alert = events.speed_limit_pre_active_alert(cp, cs, sm, metric, 0, None)

  assert alert.alert_text_1 == expected
