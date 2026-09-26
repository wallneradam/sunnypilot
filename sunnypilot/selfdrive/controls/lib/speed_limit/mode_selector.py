from cereal import car
from openpilot.common.realtime import DT_CTRL
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import Alert, AlertSize, AlertStatus, AudibleAlert, ET, Priority, VisualAlert


class SpeedLimitModeSelector:
  HOLD_SECONDS = 1.0
  BUTTONS = (car.CarState.ButtonEvent.Type.accelCruise, car.CarState.ButtonEvent.Type.decelCruise)
  LABELS = {Mode.off: 'OFF', Mode.assist: 'CONFIRM', Mode.auto: 'AUTO'}

  def __init__(self, params):
    self.params = params
    self.pressed = False
    self.frames = 0
    self.fired = False
    self.wait_for_release = False
    self.button = None
    self.suppressed_releases = set()

  def update(self, CS):
    self.suppressed_releases.clear()
    if not CS.canValid or CS.canTimeout:
      self.wait_for_release = self.wait_for_release or self.pressed
      self.pressed = False
      self.frames = 0
      return None

    for event in CS.buttonEvents:
      if event.type not in self.BUTTONS:
        continue
      if not event.pressed:
        if self.fired or self.wait_for_release:
          self.suppressed_releases.add(event.type)
        cloudlog.event('speed_limit_mode_button_release', held_seconds=round(self.frames * DT_CTRL, 2), mode_changed=self.fired)
        self.pressed = False
        self.frames = 0
        self.fired = False
        self.wait_for_release = False
        self.button = None
      elif not self.pressed and not self.wait_for_release:
        self.pressed = True
        self.frames = 0
        self.fired = False
        self.button = event.type
        cloudlog.event('speed_limit_mode_button_press', cruise_enabled=CS.cruiseState.enabled, speed_kph=round(CS.vEgo * 3.6, 1))

    if not self.pressed or self.wait_for_release:
      return None
    self.frames += 1
    if self.fired or self.frames * DT_CTRL < self.HOLD_SECONDS:
      return None

    self.fired = True
    old = self.params.get('SpeedLimitMode', return_default=True)
    if self.button == car.CarState.ButtonEvent.Type.decelCruise:
      new = Mode.off
    else:
      new = Mode.auto if old == Mode.assist else Mode.assist
    self.params.put('SpeedLimitMode', int(new), block=False)
    cloudlog.event('speed_limit_mode_changed', previous=int(old), mode=self.LABELS[new], held_seconds=round(self.frames * DT_CTRL, 2))
    alert = Alert(f'Speed limits: {self.LABELS[new]}', '', AlertStatus.normal, AlertSize.small,
                  Priority.MID, VisualAlert.none, AudibleAlert.prompt, 3.)
    alert.alert_type = 'speedLimitModeChanged/permanent'
    alert.event_type = ET.PERMANENT
    return alert
