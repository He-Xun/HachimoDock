"""Volatile DSI probe for the WiFi6 P4 v1.3 adapter, over esptool 5.x MMIO.

Pass an already connected ROM to protect() to retain the same serial
session for subsequent operations. The CLI requires an explicit port. LDO3
(2.5 V D-PHY) and the DSI host stay configured until the next SoC reset.
"""
import time

from panel_ram_common import DsiHost, PanelError, check_panel, run_cli, vo3_trim


# ESP-IDF 5.5.1, esp32p4/register/hw_ver1/soc/{reg_base,*_reg}.h
CLK = 0x500E6000
PMU = 0x50115000
EFUSE = 0x5012D000
DSI = 0x500A0000
STATUS = DSI + 0x74
HEADER = DSI + 0x6C
PAYLOAD = DSI + 0x70
RX_EMPTY = 1 << 4
READ_BUSY = 1 << 6
CMD_FULL = 1 << 1
CMD_EMPTY = 1
MAX_RETURN, DCS_READ, DCS_WRITE = 0x37, 0x06, 0x05


class Dsi(DsiHost):
    def __init__(self, esp):
        super().__init__(esp, DSI, time)

    def initialize(self):
        # Only the default F20M clock configuration is supported here.
        divider = ((self.read(CLK + 0x28) >> 16) & 255) + 1
        if divider != 24:
            raise PanelError(f"unexpected DSI reference divider {divider}")
        has_trim = self.read(EFUSE + 0x4C) & 0x1F00
        dref, mul = vo3_trim(self.read(EFUSE + 0x50) if has_trim else 0)
        # VO3 maps to PMU.ext_ldo[1]. Preserve the other PMU bits and rails.
        self.write(PMU + 0x1C4, dref << 28 | 1 << 26 | mul << 23, 0xF7800000)
        self.write(PMU + 0x1C0, 1 << 7 | 1 << 8, 0x4F80)
        time.sleep(0.020)

        # esp_lcd_new_dsi_bus, mipi_dsi_hal_init: 2 lanes, 500 Mbps.
        self.write(CLK + 0x18, 1 << 12, 1 << 12)
        self.write(CLK + 0xC0, 1 << 26, 1 << 26)
        self.write(CLK + 0xC0, 0, 1 << 26)
        self.write(CLK + 0x2C, 1 << 8, 1 << 8)
        self.write(CLK + 0x38, 0, 3 << 30)
        self.write(CLK + 0x3C, 3, 3)
        self.write(DSI + 0x04, 0)  # reset host FIFOs even on a repeated probe
        self.write(DSI + 0xA4, 1, 3)
        self.write(DSI + 0x04, 1)
        self.write(DSI + 0xA0, 1)  # PHY powered, reset asserted
        self.write(DSI + 0xA0, 3)
        self.write(DSI + 0xA0, 15)  # enable clock lane and force PLL
        # SDK PLL search selects N=2, M=50 for 20 MHz -> 500 Mbps.
        for reg, val in ((0x44, 0x0E), (0x19, 0x30), (0x17, 1),
                         (0x18, 17), (0x18, 0x81)):
            self.write(DSI + 0xB4, 0)
            self.write(DSI + 0xB8, 1 << 16 | reg)
            self.write(DSI + 0xB4, 2)
            self.write(DSI + 0xB4, 0)
            self.write(DSI + 0xB8, val)
            self.write(DSI + 0xB4, 2)
            self.write(DSI + 0xB4, 0)
        self.wait(DSI + 0xB0, 0x95, 0x95, "PHY lock and lane stop")
        self.write(DSI + 0x34, 1)  # command mode
        self.write(DSI + 0x94, 0)
        self.write(DSI + 0x9C, 50 << 16 | 104)
        self.write(DSI + 0x98, 46 << 16 | 128)
        self.write(DSI + 0x2C, 0x1D)  # HS EoTp, BTA, ECC and CRC reception
        self.write(DSI + 0x08, 6 << 8 | 3)
        for offset in range(0x78, 0x90, 4):
            self.write(DSI + offset, 0)
        self.write(DSI + 0xF4, 6000)
        self.write(DSI + 0xA4, 0x3F01)
        self.write(DSI + 0x30, 0)  # VC 0
        self.write(DSI + 0x68, 1 << 24 | 1 << 18 | 1 << 16)
        self.write(DSI + 0x94, 1)  # HS clock; DCS transfers are LP
        time.sleep(0.030)

def protect(esp, *, probe_only=False):
    """Return measured IDs and modes. Raise on unknown identity or unconfirmed sleep."""
    if esp.CHIP_NAME != "ESP32-P4" or esp.secure_download_mode or esp.get_chip_revision() != 103:
        raise PanelError("requires an unrestricted ESP32-P4 v1.3 downloader")
    bus = Dsi(esp)
    bus.initialize()
    return check_panel(bus, probe_only)


def main():
    run_cli(protect, __doc__)


if __name__ == "__main__":
    main()
