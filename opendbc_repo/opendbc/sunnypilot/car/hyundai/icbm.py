"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from opendbc.car import DT_CTRL, structs
from opendbc.car.can_definitions import CanData
from opendbc.car.hyundai import hyundaican, hyundaicanfd
from opendbc.car.hyundai.values import HyundaiFlags, Buttons, CANFD_CAR, CAR
from opendbc.sunnypilot.car.intelligent_cruise_button_management_interface_base import IntelligentCruiseButtonManagementInterfaceBase

ButtonType = structs.CarState.ButtonEvent.Type
SendButtonState = structs.IntelligentCruiseButtonManagement.SendButtonState

BUTTON_COPIES = 2
BUTTON_COPIES_TIME = 7
BUTTON_COPIES_TIME_IMPERIAL = [BUTTON_COPIES_TIME + 3, 70]
BUTTON_COPIES_TIME_METRIC = [BUTTON_COPIES_TIME, 40]
BUTTON_TIMING = (0.1, 0.15)  # seconds: release gap and complete cycle
IONIQ_STEP_FRAMES = round(0.12 / DT_CTRL)
IONIQ_MIN_STEP_FRAMES = round(0.10 / DT_CTRL)
IONIQ_PRESS_FRAMES = round(0.05 / DT_CTRL)
IONIQ_RELEASE_FRAMES = round(0.05 / DT_CTRL)
IONIQ_RETRY_FRAMES = round(0.11 / DT_CTRL)
IONIQ_START_WAIT_FRAMES = round(0.08 / DT_CTRL)
IONIQ_ACK_FRAMES = round(0.09 / DT_CTRL)

BUTTONS = {
  SendButtonState.increase: Buttons.RES_ACCEL,
  SendButtonState.decrease: Buttons.SET_DECEL,
}


class IntelligentCruiseButtonManagementInterface(IntelligentCruiseButtonManagementInterfaceBase):
  def __init__(self, CP, CP_SP):
    super().__init__(CP, CP_SP)
    self._reset_ioniq_button()

  def create_can_mock_button_messages(self, packer, CS, send_button) -> list[CanData]:
    can_sends = []
    copies_xp = BUTTON_COPIES_TIME_METRIC if CS.is_metric else BUTTON_COPIES_TIME_IMPERIAL
    copies = int(np.interp(BUTTON_COPIES_TIME, copies_xp, [1, BUTTON_COPIES]))

    if self.CP.carFingerprint == CAR.HYUNDAI_IONIQ_EV_LTD:
      return self.create_ioniq_mock_button_messages(packer, CS, send_button, copies)

    button_wait, button_interval = BUTTON_TIMING
    if (self.frame - self.last_button_frame) * DT_CTRL > button_wait:
      can_sends.extend([hyundaican.create_clu11(packer, self.frame, CS.clu11, send_button, self.CP)] * copies)
      if (self.frame - self.last_button_frame) * DT_CTRL >= button_interval:
        self.last_button_frame = self.frame

    return can_sends

  def _reset_ioniq_button(self) -> None:
    self._ioniq_button = None
    self._ioniq_speed = None
    self._ioniq_press_frame = None
    self._ioniq_next_press_frame = 0
    self._ioniq_ack_frames = float(IONIQ_ACK_FRAMES)

  def create_ioniq_mock_button_messages(self, packer, CS, send_button, copies) -> list[CanData]:
    state = CS.out
    speed = state.cruiseState.speedCluster
    manual_button = any(b.pressed and b.type in (ButtonType.accelCruise, ButtonType.decelCruise) for b in state.buttonEvents)
    if (not state.canValid or state.canTimeout or not state.cruiseState.enabled
        or not np.isfinite(speed) or speed < 0 or manual_button):
      self._reset_ioniq_button()
      return []

    if send_button != self._ioniq_button:
      self._ioniq_button = send_button
      self._ioniq_speed = speed
      self._ioniq_press_frame = None
      self._ioniq_next_press_frame = max(self.frame, self.last_button_frame + IONIQ_START_WAIT_FRAMES)

    direction = 1 if send_button == Buttons.RES_ACCEL else -1
    speed_change = speed - self._ioniq_speed
    if abs(speed_change) > 0.001:
      self._ioniq_speed = speed
      if self._ioniq_press_frame is not None and speed_change * direction > 0:
        response_frames = min(self.frame - self._ioniq_press_frame, IONIQ_RETRY_FRAMES)
        self._ioniq_ack_frames = 0.9 * self._ioniq_ack_frames + 0.1 * max(1, response_frames)
        press_frame = self._ioniq_press_frame
        self._ioniq_press_frame = None
        # Aim for one step interval between the reported speed changes.
        self._ioniq_next_press_frame = max(
          press_frame + IONIQ_MIN_STEP_FRAMES,
          self.frame + max(1, IONIQ_STEP_FRAMES - round(self._ioniq_ack_frames)),
        )
      elif speed_change * direction < 0:
        self._ioniq_press_frame = None
        self._ioniq_next_press_frame = self.frame + IONIQ_START_WAIT_FRAMES

    if self._ioniq_press_frame is None or self.frame - self._ioniq_press_frame >= IONIQ_RETRY_FRAMES:
      if self.frame < self._ioniq_next_press_frame or self.frame - self.last_button_frame < IONIQ_RELEASE_FRAMES:
        return []
      self._ioniq_press_frame = self.frame
      self._ioniq_next_press_frame = self.frame + IONIQ_RETRY_FRAMES

    if self.frame - self._ioniq_press_frame >= IONIQ_PRESS_FRAMES:
      return []

    self.last_button_frame = self.frame
    return [hyundaican.create_clu11(packer, self.frame, CS.clu11, send_button, self.CP)] * copies

  def create_canfd_mock_button_messages(self, packer, CS, CAN, send_button) -> list[CanData]:
    can_sends = []
    if self.CP.flags & HyundaiFlags.CANFD_ALT_BUTTONS:
      # TODO: resume for alt button cars
      pass
    else:
      if (self.frame - self.last_button_frame) * DT_CTRL > 0.2:
        self.button_frame += 1
        button_counter_offset = [1, 1, 0, None][self.button_frame % 4]
        if button_counter_offset is not None:
          for _ in range(20):
            can_sends.append(hyundaicanfd.create_buttons(packer, self.CP, CAN, (CS.buttons_counter + button_counter_offset) % 0xF, send_button))
          self.last_button_frame = self.frame

    return can_sends

  def update(self, CS, CC_SP, packer, frame, last_button_frame, CAN) -> list[CanData]:
    can_sends = []
    self.CC_SP = CC_SP
    self.ICBM = CC_SP.intelligentCruiseButtonManagement
    self.frame = frame
    self.last_button_frame = last_button_frame

    if self.ICBM.sendButton == SendButtonState.none:
      if self.CP.carFingerprint == CAR.HYUNDAI_IONIQ_EV_LTD:
        self._reset_ioniq_button()
      return can_sends

    send_button = BUTTONS[self.ICBM.sendButton]
    if self.CP.carFingerprint in CANFD_CAR:
      can_sends.extend(self.create_canfd_mock_button_messages(packer, CS, CAN, send_button))
    else:
      can_sends.extend(self.create_can_mock_button_messages(packer, CS, send_button))

    return can_sends
