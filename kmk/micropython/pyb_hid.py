import logging

from pyb import USB_HID, delay, hid_keyboard

from kmk.common.consts import HID_REPORT_STRUCTURE, HIDReportTypes
from kmk.common.event_defs import HID_REPORT_EVENT
from kmk.common.keycodes import (FIRST_KMK_INTERNAL_KEYCODE, ConsumerKeycode,
                                 ModifierKeycode)
from kmk.common.macros import KMKMacro


def generate_pyb_hid_descriptor():
    existing_keyboard = list(hid_keyboard)
    existing_keyboard[-1] = HID_REPORT_STRUCTURE
    return tuple(existing_keyboard)


class HIDHelper:
    def __init__(self, store, log_level=logging.NOTSET):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)

        self.store = store
        self.store.subscribe(
            lambda state, action: self._subscription(state, action),
        )

        self._hid = USB_HID()

        # For some bizarre reason this can no longer be 8, it'll just fail to
        # send anything. This is almost certainly a bug in the report descriptor
        # sent over in the boot process. For now the sacrifice is that we only
        # support 5KRO until I figure this out, rather than the 6KRO HID defines.
        self._evt = bytearray(7)
        self.report_device = memoryview(self._evt)[0:1]

        # Landmine alert for HIDReportTypes.KEYBOARD: byte index 1 of this view
        # is "reserved" and evidently (mostly?) unused. However, other modes (or
        # at least consumer, so far) will use this byte, which is the main reason
        # this view exists. For KEYBOARD, use report_mods and report_non_mods
        self.report_keys = memoryview(self._evt)[1:]

        self.report_mods = memoryview(self._evt)[1:2]
        self.report_non_mods = memoryview(self._evt)[3:]

    def _subscription(self, state, action):
        if action.type == HID_REPORT_EVENT:
            self.clear_all()

            consumer_key = None
            for key in state.keys_pressed:
                if isinstance(key, ConsumerKeycode):
                    consumer_key = key
                    break

            reporting_device = self.report_device[0]
            needed_reporting_device = HIDReportTypes.KEYBOARD

            if consumer_key:
                needed_reporting_device = HIDReportTypes.CONSUMER

            if reporting_device != needed_reporting_device:
                # If we are about to change reporting devices, release
                # all keys and close our proverbial tab on the existing
                # device, or keys will get stuck (mostly when releasing
                # media/consumer keys)
                self.send()

            self.report_device[0] = needed_reporting_device

            if consumer_key:
                self.add_key(consumer_key)
            else:
                for key in state.keys_pressed:
                    if isinstance(key, KMKMacro) or key.code >= FIRST_KMK_INTERNAL_KEYCODE:
                        continue

                    if isinstance(key, ModifierKeycode):
                        self.add_modifier(key)
                    else:
                        self.add_key(key)

                        if key.has_modifiers:
                            for mod in key.has_modifiers:
                                self.add_modifier(mod)

            self.send()

    def send(self):
        self.logger.debug('Sending HID report: {}'.format(self._evt))
        self._hid.send(self._evt)

        # Without this delay, events get clobbered and you'll likely end up with
        # a string like `heloooooooooooooooo` rather than `hello`. This number
        # may be able to be shrunken down. It may also make sense to use
        # time.sleep_us or time.sleep_ms or time.sleep (platform dependent)
        # on non-Pyboards.
        #
        # It'd be real awesome if pyb.USB_HID.send/recv would support
        # uselect.poll or uselect.select to more safely determine when
        # it is safe to write to the host again...
        delay(5)

        return self

    def clear_all(self):
        for idx, _ in enumerate(self.report_keys):
            self.report_keys[idx] = 0x00

        return self

    def clear_non_modifiers(self):
        for idx, _ in enumerate(self.report_non_mods):
            self.report_non_mods[idx] = 0x00

        return self

    def add_modifier(self, modifier):
        if isinstance(modifier, ModifierKeycode):
            self.report_mods[0] |= modifier.code
        else:
            self.report_mods[0] |= modifier

        return self

    def remove_modifier(self, modifier):
        if isinstance(modifier, ModifierKeycode):
            self.report_mods[0] ^= modifier.code
        else:
            self.report_mods[0] ^= modifier

        return self

    def add_key(self, key):
        # Try to find the first empty slot in the key report, and fill it
        placed = False

        where_to_place = self.report_non_mods

        if self.report_device[0] == HIDReportTypes.CONSUMER:
            where_to_place = self.report_keys

        for idx, _ in enumerate(where_to_place):
            if where_to_place[idx] == 0x00:
                where_to_place[idx] = key.code
                placed = True
                break

        if not placed:
            self.logger.warning('Out of space in HID report, could not add key')

        return self

    def remove_key(self, key):
        removed = False

        where_to_place = self.report_non_mods

        if self.report_device[0] == HIDReportTypes.CONSUMER:
            where_to_place = self.report_keys

        for idx, _ in enumerate(where_to_place):
            if where_to_place[idx] == key.code:
                where_to_place[idx] = 0x00
                removed = True

        if not removed:
            self.logger.warning('Tried to remove key that was not added')

        return self
