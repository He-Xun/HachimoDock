"""DCS transactions for the P4 DSI host and the NV3049F panel guard."""
import argparse
import json
import struct
import time


RX_EMPTY = 1 << 4
READ_BUSY = 1 << 6
CMD_FULL = 1 << 1
CMD_EMPTY = 1
MAX_RETURN, DCS_READ, DCS_WRITE = 0x37, 0x06, 0x05


class PanelError(RuntimeError):
    pass


class PanelIdentityUnknown(PanelError):
    """The display identity could not be established on a working ROM connection."""


class DsiWaitTimeout(PanelError):
    pass


class NoPanelReply(DsiWaitTimeout):
    """No peripheral reply and no reported DSI transport error."""

class DamagedPanelReply(PanelError):
    """A reply consumed in full, but flagged by the DSI host."""


def vo3_trim(calibration):
    """ldo_ll_voltage_to_dref_mul(unit=2, voltage_mv=2500), including tie order."""
    def coefficient(raw, sign_bit, offset, default):
        if not raw:
            return default
        magnitude = raw & (sign_bit - 1)
        return offset + (-magnitude if raw & sign_bit else magnitude)

    k = coefficient((calibration >> 6) & 255, 128, 975, 1000)
    vos = coefficient((calibration >> 14) & 63, 32, -3, 0)
    c = coefficient((calibration >> 20) & 63, 32, 990, 1000)
    choices = []
    for dref in range(16):
        reference = 10 + dref if dref < 9 else 20 + 2 * (dref - 9)
        for mul in range(8):
            voltage = (reference * k + 20 * vos) * (4000 + mul * c)
            choices.append((abs(2500 * 80000 - voltage), dref, mul))
    _, dref, mul = min(choices)
    return dref, mul


class DsiHost:
    def __init__(self, esp, host, clock=time):
        from esptool.targets.esp32p4 import ESP32P4ROM

        # StubLoader inherits from ESP32P4ROM. Require a ROM session and the
        # result of esptool's SYNC check before touching the DSI peripherals.
        if (not isinstance(esp, ESP32P4ROM)
                or getattr(esp, "IS_STUB", None) is not False
                or getattr(esp, "sync_stub_detected", None) is not False
                or getattr(esp, "secure_download_mode", None) is not False):
            raise PanelError("paired DSI reads require a connected ESP32-P4 ROM downloader")
        self.esp = esp
        self.host = host
        self.clock = clock
        self.read = esp.read_reg
        self.write_word = esp.write_reg

    def write(self, addr, value, mask=0xFFFFFFFF):
        # This downloader session is the sole peripheral owner.
        if mask != 0xFFFFFFFF:
            value = (self.read(addr) & ~mask) | (value & mask)
        self.write_word(addr, value)

    def wait(self, addr, mask, expected, label):
        deadline = self.clock.monotonic() + 0.5
        while True:
            value = self.read(addr)
            if value & mask == expected:
                return
            if self.clock.monotonic() >= deadline:
                raise DsiWaitTimeout(f"{label} timeout at {addr:#x}, last value {value:#x}")
            self.clock.sleep(0.002)

    def packet(self, kind, data):
        self.wait(self.host + 0x74, CMD_FULL, 0, "command FIFO")
        self.write(self.host + 0x6C, data << 8 | kind)

    def register(self, reg, size=1):
        # Refuse a stale or surplus payload instead of attributing it to an ID.
        self.wait(self.host + 0x74, READ_BUSY | CMD_FULL | CMD_EMPTY, CMD_EMPTY, "previous DSI command")
        if not self.read(self.host + 0x74) & RX_EMPTY:
            raise PanelError(f"unexpected pending payload before DCS {reg:#04x}")
        # Keep MRPS and RD in one ROM command. A serial round trip between
        # headers leaves rev302 waiting for a reply on the first RDID.
        header = self.host + 0x6C
        payload = struct.pack(
            "<8I", header, size << 8 | MAX_RETURN, 0xFFFFFFFF, 2,
            header, reg << 8 | DCS_READ, 0xFFFFFFFF, 0,
        )
        self.esp.check_command("paired DSI headers", self.esp.ESP_CMDS["WRITE_REG"], payload)
        try:
            self.wait(self.host + 0x74, RX_EMPTY, 0, f"DCS {reg:#04x} reply")
        except DsiWaitTimeout as exc:
            if not (self.read(self.host + 0xBC) & 0x1FFFFF or self.read(self.host + 0xC0) & 0x1FFF):
                raise NoPanelReply(str(exc)) from exc
            raise
        value = self.read(self.host + 0x70) & ((1 << (8 * size)) - 1)
        self.wait(self.host + 0x74, READ_BUSY, 0, "read completion")
        errors = self.read(self.host + 0xBC) & 0x1FFFFF
        errors1 = self.read(self.host + 0xC0) & 0x1FFF
        if not self.read(self.host + 0x74) & RX_EMPTY:
            raise PanelError(f"invalid DCS {reg:#04x} reply, INT_ST={errors:#x}/{errors1:#x}")
        if errors or errors1:
            raise DamagedPanelReply(f"invalid DCS {reg:#04x} reply, INT_ST={errors:#x}/{errors1:#x}")
        return value


def read_panel_id(bus):
    # Some panels return a short first reply while the command bus settles.
    # Repeat the whole set once so values from different attempts cannot be mixed.
    damaged = None
    for attempt in range(2):
        values = []
        for reg, size in ((0x04, 3), (0xDA, 1), (0xDB, 1), (0xDC, 1)):
            try:
                values.append(bus.register(reg, size))
            except NoPanelReply:
                values.append(None)
            except DamagedPanelReply as exc:
                if attempt or reg != 0x04:
                    raise
                damaged = exc
                values.append(None)
        combined, *individual = values
        if ((combined == 0x034930 and individual == [0x30, 0x49, 0x03])
                or (combined == 0xfba080 and individual == [0x80, 0xa0, 0xfb])
                or (combined == 0xFFFFFF and individual == [255, 255, 255])):
            return combined, individual
        if attempt == 0:
            bus.clock.sleep(0.030)
    if damaged is not None:
        raise damaged
    raise PanelIdentityUnknown(f"ID mismatch or reply timeout: RDID/DA/DB/DC={values}")


def check_panel(bus, probe_only):
    combined, individual = read_panel_id(bus)
    if combined == 0xfba080:
        return {"rdid": "80a0fb", "individual": bytes(individual).hex(),
                "identification": "st7102-id", "commands": [], "confirmed": False}
    default_id = combined == 0xFFFFFF
    try:
        before = bus.register(0x0A)
    except NoPanelReply as exc:
        if default_id:
            raise PanelIdentityUnknown(f"default panel ID; {exc}") from exc
        raise
    if default_id and before not in (0x9C, 0x98, 0x08):
        raise PanelIdentityUnknown(f"default panel ID and invalid DCS power mode {before:#04x}")
    result = {"rdid": combined.to_bytes(3, "little").hex(), "individual": bytes(individual).hex(),
              "identification": "default-id-power-mode" if default_id else "nv3049f-id",
              "before": before, "commands": [], "confirmed": False}
    if probe_only:
        return result
    if before != 0x08:
        bus.packet(DCS_WRITE, 0x28)
        bus.clock.sleep(0.050)
        if default_id and bus.register(0x0A) != 0x98:
            raise PanelError("panel did not confirm display-off before sleep")
        bus.packet(DCS_WRITE, 0x10)
        bus.clock.sleep(0.250)
        result["commands"] = [0x28, 0x10]
    first = bus.register(0x0A)
    bus.clock.sleep(0.100)
    second = bus.register(0x0A)
    if (first, second) != (8, 8):
        raise PanelError(f"sleep unconfirmed: commands={result['commands']}, modes={first:#04x}/{second:#04x}")
    return dict(result, readback=[first, second], confirmed=True)


def run_cli(protect, description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--port", required=True)
    parser.add_argument("--probe-only", action="store_true", help="read IDs and power mode only")
    parser.add_argument("--already-in-rom", action="store_true", help="sync without requesting reset")
    args = parser.parse_args()
    import esptool
    from serial import SerialException

    try:
        mode = "no-reset" if args.already_in_rom else "default-reset"
        with esptool.detect_chip(args.port, baud=115200, connect_mode=mode, connect_attempts=1) as esp:
            result = protect(esp, probe_only=args.probe_only)
    except (PanelError, esptool.FatalError, SerialException, OSError) as exc:
        parser.exit(1, f"{parser.prog}: {exc}\n")
    print(json.dumps(result))
