"""Exercise the standalone v1.3 guard at the esptool register interface."""
from pathlib import Path
import struct
import sys

import pytest
from esptool.targets.esp32p4 import ESP32P4ROM

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import panel_ram_v13 as panel


class FakeRom(ESP32P4ROM):
    CHIP_NAME = "ESP32-P4"
    secure_download_mode = False
    revision = 103

    def __init__(self):
        self.regs = {panel.CLK + 0x28: 23 << 16, panel.DSI + 0xB0: 0x95}
        self.ids = {4: 0x034930, 0xDA: 0x30, 0xDB: 0x49, 0xDC: 3}
        self.modes = [0x9C, 8, 8]
        self.payload = None
        self.commands = []
        self.read_commands = []
        self.mmio_writes = []
        self.initial_rdid = None
        self.drop_reply = None

    def get_chip_revision(self):
        return self.revision

    def read_reg(self, reg):
        if reg == panel.STATUS:
            return panel.CMD_EMPTY | (panel.RX_EMPTY if self.payload is None else 0)
        if reg == panel.PAYLOAD:
            value, self.payload = self.payload, None
            assert value is not None
            return value
        return self.regs.get(reg, 0)

    def write_reg(self, reg, value, mask=0xFFFFFFFF):
        self.mmio_writes.append(reg)
        if reg != panel.HEADER:
            self.regs[reg] = self.regs.get(reg, 0) & ~mask | value & mask
            return
        kind, data = value & 255, value >> 8
        # DCS writes may be individual. Reads must issue both headers at once.
        assert kind == 0x05
        self.commands.append(data)

    def check_command(self, description, opcode, payload):
        assert opcode == 0x09
        hdr1, mrps, mask1, delay1, hdr2, rd, mask2, delay2 = struct.unpack("<8I", payload)
        reg = rd >> 8
        size = 3 if reg == 4 else 1
        assert (hdr1, mrps, mask1, delay1) == (panel.HEADER, size << 8 | 0x37, 0xFFFFFFFF, 2)
        assert (hdr2, rd, mask2, delay2) == (panel.HEADER, reg << 8 | 6, 0xFFFFFFFF, 0)
        self.read_commands.append(reg)
        if reg == self.drop_reply:
            return
        if reg == 4 and self.initial_rdid is not None:
            self.payload, self.initial_rdid = self.initial_rdid, None
        else:
            self.payload = self.modes.pop(0) if reg == 0x0A else self.ids[reg]


@pytest.fixture
def rom(monkeypatch):
    class Clock:
        now = 0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds
    monkeypatch.setattr(panel, "time", Clock())
    return FakeRom()


def test_awake_guard(rom):
    result = panel.protect(rom)
    assert result["confirmed"] and result["readback"] == [8, 8]
    assert result["identification"] == "nv3049f-id"
    assert result["rdid"] == result["individual"] == "304903"
    assert rom.commands == [0x28, 0x10]
    assert rom.read_commands == [4, 0xDA, 0xDB, 0xDC, 10, 10, 10]
    assert set(rom.mmio_writes) <= {
        panel.PMU + 0x1C0, panel.PMU + 0x1C4,
        *(panel.CLK + off for off in [0x18, 0x28, 0x2C, 0x38, 0x3C, 0xC0]),
        *(panel.DSI + off for off in range(0, 0x100, 4)),
    }


@pytest.mark.parametrize("reg", [4, 0xDA, 0xDB, 0xDC])
def test_both_ids_required(rom, reg):
    rom.ids[reg] ^= 1
    with pytest.raises(panel.PanelError, match="ID mismatch"):
        panel.protect(rom)
    assert not rom.commands


def test_st7102_rechecks_first_damaged_rdid_without_sleep(rom, monkeypatch):
    rom.ids = {4: 0xfba080, 0xda: 0x80, 0xdb: 0xa0, 0xdc: 0xfb}
    read = rom.read_reg
    def transient_status(reg):
        if reg == panel.DSI + 0xbc and rom.read_commands == [4]:
            return 0x1f00
        return read(reg)
    monkeypatch.setattr(rom, "read_reg", transient_status)
    result = panel.protect(rom)
    assert result["identification"] == "st7102-id"
    assert result["rdid"] == result["individual"] == "80a0fb"
    assert rom.read_commands == [4, 0xda, 0xdb, 0xdc] * 2
    assert not rom.commands


@pytest.mark.parametrize("reg", [4, 0xDA, 0xDB, 0xDC, 0x0A])
def test_missing_reply(rom, reg):
    rom.drop_reply = reg
    with pytest.raises(panel.PanelError, match="timeout"):
        panel.protect(rom)
    assert not rom.commands


def test_probe_and_already_asleep(rom):
    assert not panel.protect(rom, probe_only=True)["confirmed"]
    rom.modes = [8, 8, 8]
    assert panel.protect(rom)["confirmed"]
    assert not rom.commands


@pytest.mark.parametrize("modes", [[0x9C, 0, 8], [0x9C, 8, 0x9C], [8, 8, 0]])
def test_failed_confirmation(rom, modes):
    rom.modes = modes.copy()
    with pytest.raises(panel.PanelError, match="sleep unconfirmed"):
        panel.protect(rom)
    assert rom.commands == ([] if modes[0] == 8 else [0x28, 0x10])


@pytest.mark.parametrize("attr,value", [("revision", 300), ("CHIP_NAME", "ESP32"),
                                        ("secure_download_mode", True)])
def test_wrong_target(rom, attr, value):
    setattr(rom, attr, value)
    with pytest.raises(panel.PanelError):
        panel.protect(rom)
    assert not rom.mmio_writes


def test_not_locked(rom):
    rom.regs[panel.DSI + 0xB0] = 1
    with pytest.raises(panel.PanelError, match="lane stop"):
        panel.protect(rom)
    assert not rom.read_commands and not rom.commands


def test_stale_or_damaged_reply(rom):
    rom.payload = 0x034930
    with pytest.raises(panel.PanelError, match="pending payload"):
        panel.protect(rom)
    rom.payload = None
    rom.regs[panel.DSI + 0xC0] = 16
    with pytest.raises(panel.PanelError, match="invalid DCS"):
        panel.protect(rom)
    assert not rom.commands


def test_vo3(rom):
    assert panel.vo3_trim(0) == (9, 6)
    rom.regs[panel.EFUSE + 0x4C] = 1 << 8
    rom.regs[panel.EFUSE + 0x50] = 0x0163F100
    rom.regs[panel.PMU + 0x1C4] = 1 << 27
    panel.protect(rom, probe_only=True)
    assert rom.regs[panel.PMU + 0x1C4] == (11 << 28 | 1 << 27 | 1 << 26 | 5 << 23)

@pytest.mark.parametrize("initial,full,db,expected_reread,accepted", [
    (0x30, 0x034930, 0x49, True, True),
    (0x30, 0x30, 0x49, True, False),
    (0x30, 0x034931, 0x49, True, False),
    (0x30, 0x034930, 0x48, True, False),
    (0x31, 0x034930, 0x49, True, True),
])
def test_partial_first_rdid(rom, initial, full, db, expected_reread, accepted):
    rom.initial_rdid = initial
    rom.ids.update({4: full, 0xDB: db})
    if accepted:
        assert panel.protect(rom)["confirmed"]
    else:
        with pytest.raises(panel.PanelError, match="ID mismatch"):
            panel.protect(rom)
        assert not rom.commands
        assert 10 not in rom.read_commands
    assert rom.read_commands.count(4) == 1 + int(expected_reread)
    if expected_reread:
        assert rom.read_commands[:5] == [4, 0xDA, 0xDB, 0xDC, 4]


@pytest.mark.parametrize("reg", [4, 0xDA, 0xDB, 0xDC])
def test_single_missing_reply_recovers_without_legacy_fallback(rom, reg):
    transaction = rom.check_command
    def lose_first_reply(description, opcode, payload):
        first = reg not in rom.read_commands
        rom.drop_reply = reg if first else None
        transaction(description, opcode, payload)
    rom.check_command = lose_first_reply
    result = panel.protect(rom)
    assert result["identification"] == "nv3049f-id" and result["confirmed"]
    assert rom.read_commands[:8] == [4, 0xDA, 0xDB, 0xDC] * 2


@pytest.mark.parametrize("flag,value", [
    ("IS_STUB", True), ("sync_stub_detected", True),
    ("IS_STUB", None), ("sync_stub_detected", None),
])
def test_reject_stub_or_unknown_loader_before_writes(rom, flag, value):
    setattr(rom, flag, value)
    with pytest.raises(panel.PanelError, match="ROM downloader"):
        panel.protect(rom)
    assert not rom.mmio_writes and not rom.read_commands


def test_reject_esptool_stub_class():
    from esptool.targets.esp32p4 import ESP32P4StubLoader

    # Allocate only the Python object; no loader initialization or serial port.
    stub = object.__new__(ESP32P4StubLoader)
    with pytest.raises(panel.PanelError, match="ROM downloader"):
        panel.Dsi(stub)


def test_failed_paired_command_has_no_fallback(rom):
    attempted = []

    def rejected(description, opcode, payload):
        attempted.append(payload)
        raise RuntimeError("ROM rejected WRITE_REG")

    rom.check_command = rejected
    with pytest.raises(RuntimeError, match="rejected"):
        panel.protect(rom)
    assert len(attempted) == 1
    assert panel.HEADER not in rom.mmio_writes
    assert not rom.commands


@pytest.mark.parametrize("combined,da,missing", [
    (0, 0, 4), (0, 0, 0xDA), (0, 0, None), (0x30, 0, 0xDA),
    (0x034930, 0, 0xDA), (0, 0x30, 0xDB), (0x034930, 0, None),
])
def test_persistent_inconclusive_id_is_unknown(rom, combined, da, missing):
    from panel_ram_common import PanelIdentityUnknown
    rom.ids.update({4: combined, 0xDA: da, 0xDB: 0, 0xDC: 0})
    rom.drop_reply = missing
    with pytest.raises(PanelIdentityUnknown):
        panel.protect(rom)
    assert rom.read_commands == [4, 0xDA, 0xDB, 0xDC] * 2
    assert not rom.commands


@pytest.mark.parametrize("fault", ["transport_status", "read_power", "sleep_readback"])
def test_link_or_sleep_error_never_classified_as_unknown_id(rom, fault):
    from panel_ram_common import PanelIdentityUnknown
    if fault == "transport_status":
        rom.drop_reply = 4
        rom.regs[panel.DSI + 0xBC] = 1
    elif fault == "read_power":
        rom.drop_reply = 0x0A
    else:
        rom.modes = [0x9C, 8, 0]
    with pytest.raises(panel.PanelError) as exc:
        panel.protect(rom)
    assert not isinstance(exc.value, PanelIdentityUnknown)

@pytest.mark.parametrize("initial", [0xFF, 0xFFFF])
def test_short_default_id_reread(rom, initial):
    rom.initial_rdid = initial
    rom.ids = {4: 0xFFFFFF, 0xDA: 255, 0xDB: 255, 0xDC: 255}
    rom.modes = [0x9C, 0x98, 8, 8]
    result = panel.protect(rom)
    assert result["identification"] == "default-id-power-mode" and result["confirmed"]
    assert rom.read_commands[:5] == [4, 0xDA, 0xDB, 0xDC, 4]


@pytest.mark.parametrize("combined,dc,missing", [
    (0xFF, 255, None), (0xFFFFFF, 255, 0xDA),
    (0xFFFFFF, 0, None), (0xFFFFFF, 255, 0x0A),
])
def test_unresolved_default_id_is_unknown(rom, combined, dc, missing):
    from panel_ram_common import PanelIdentityUnknown
    rom.ids = {4: combined, 0xDA: 255, 0xDB: 255, 0xDC: dc}
    rom.drop_reply = missing
    with pytest.raises(PanelIdentityUnknown):
        panel.protect(rom)
    assert not rom.commands


@pytest.mark.parametrize("raw", [bytes.fromhex("7102a2"), bytes.fromhex("770100")])
def test_vendor_init_bytes_are_not_panel_signatures(rom, raw):
    from panel_ram_common import PanelIdentityUnknown
    rom.ids = {4: int.from_bytes(raw, "little"), 0xDA: raw[0], 0xDB: raw[1], 0xDC: raw[2]}
    with pytest.raises(PanelIdentityUnknown):
        panel.protect(rom)
    assert rom.read_commands == [4, 0xDA, 0xDB, 0xDC] * 2
    assert not rom.commands
