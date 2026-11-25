"""Volatile NV3049F guard for WiFi6 ESP32-P4 rev302, ESP-IDF 5.5.4 hw_ver3.

Use protect(esp) with a connected esptool ROM to keep the same session.
Probe-only also initializes VO3 and DSI. Neither mode accesses SPI flash.
"""
import time

from panel_ram_common import DsiHost, PanelError, check_panel, run_cli, vo3_trim


# hw_ver3/soc/reg_base.h, hp_sys_clkrst_reg.h and mipi_dsi_bridge_reg.h
HOST = 0x500A0000
BRIDGE_EN = 0x500A0804
SOC_CLK_CTRL1 = 0x500E6018
REF_CLK_CTRL1 = 0x500E6028
REF_CLK_CTRL2 = 0x500E602C
PERI_CLK_CTRL02 = 0x500E6038
PERI_CLK_CTRL03 = 0x500E603C
HP_RST_EN0 = 0x500E60C0
PLLREF_FIELDS = (7 << 16) | (255 << 19)
# hw_ver3/soc/efuse_reg.h; only RD_MAC_SYS_2/3 are read.
BLOCK_VERSION = 0x5012D04C
VO3_CALIBRATION = 0x5012D050
# ldo_ll.h: LDO_ID2UNIT(3) -> 2 -> PMU.ext_ldo[1], P0_0P2A.
VO3_CTRL = 0x501151C0
VO3_ANA = 0x501151C4


class Dsi(DsiHost):
    def set_checked(self, address, mask, value):
        self.write(address, value, mask)
        if self.read(address) & mask != value:
            raise PanelError(f"configuration readback failed at {address:#x}, mask={mask:#x}")

    def enable_vo3(self):
        version = self.read(BLOCK_VERSION)
        calibration = self.read(VO3_CALIBRATION) if version & 0x1F00 else 0
        dref, mul = vo3_trim(calibration)
        # Follow esp_ldo_acquire_channel: voltage, owner, ripple suppression, enable.
        self.set_checked(VO3_CTRL, 1 << 14, 0)  # disable rail bypass
        self.set_checked(VO3_ANA, (15 << 28) | (7 << 23), dref << 28 | mul << 23)
        self.set_checked(VO3_CTRL, (1 << 7) | (7 << 9), 1 << 7)
        self.set_checked(VO3_ANA, 1 << 26, 1 << 26)
        self.set_checked(VO3_CTRL, 1 << 8, 1 << 8)
        self.clock.sleep(0.020)

    def initialize(self):
        # F20M is the configuration clock only. Its SPLL divider is shared.
        if (self.read(REF_CLK_CTRL1) >> 16) & 255 != 23:
            raise PanelError("expected the WiFi6 480/24 MHz PHY configuration clock")
        self.enable_vo3()
        self.write(SOC_CLK_CTRL1, 1 << 12, 1 << 12)
        self.write(HP_RST_EN0, 1 << 26, 1 << 26)
        self.write(HP_RST_EN0, 0, 1 << 26)
        self.write(REF_CLK_CTRL2, 1 << 8, 1 << 8)
        self.write(PERI_CLK_CTRL02, 0, 3 << 30)  # config source F20M
        self.write(PERI_CLK_CTRL03, 1, 1)
        # Rev >= 300 has independent PLLREF mux/divider. XTAL=0, div-1=0.
        self.set_checked(PERI_CLK_CTRL03, PLLREF_FIELDS, 0)
        self.set_checked(PERI_CLK_CTRL03, 2, 2)

        self.write(HOST + 0x04, 0)
        self.write(HOST + 0xA4, 1, 3)  # two data lanes
        self.write(HOST + 0x04, 1)
        self.write(HOST + 0xA0, 1)
        self.write(HOST + 0xA0, 3)
        self.write(HOST + 0xA0, 15)
        self.write(BRIDGE_EN, 2, 2)
        self.write(BRIDGE_EN, 0, 2)
        # mipi_dsi_hal_configure_phy_pll: 40 MHz XTAL, M=50, N=4.
        # The 500..549 Mbps range is 0x07, encoded as 0x0E.
        for reg, value in ((0x44, 0x0E), (0x19, 0x30), (0x17, 3),
                           (0x18, 17), (0x18, 0x81)):
            self.write(HOST + 0xB4, 0)
            self.write(HOST + 0xB8, 1 << 16 | reg)
            self.write(HOST + 0xB4, 2)
            self.write(HOST + 0xB4, 0)
            self.write(HOST + 0xB8, value)
            self.write(HOST + 0xB4, 2)
            self.write(HOST + 0xB4, 0)
        self.wait(HOST + 0xB0, 0x95, 0x95, "PHY lock and two stopped data lanes")
        self.write(HOST + 0x34, 1)
        self.write(HOST + 0x94, 0)
        self.write(HOST + 0x9C, 50 << 16 | 104)
        self.write(HOST + 0x98, 46 << 16 | 128)
        self.write(HOST + 0x2C, 0x1D)
        self.write(HOST + 0x08, 6 << 8 | 3)  # round(62.5/10), round(62.5/18)
        for offset in range(0x78, 0x90, 4):
            self.write(HOST + offset, 0)
        self.write(HOST + 0xF4, 6000)
        self.write(HOST + 0xA4, 0x3F01)
        self.write(HOST + 0x30, 0)
        self.write(HOST + 0x68, 1 << 24 | 1 << 18 | 1 << 16)
        self.write(HOST + 0x94, 1)
        self.clock.sleep(0.030)


def protect(esp, *, probe_only=False):
    if esp.CHIP_NAME != "ESP32-P4" or esp.secure_download_mode or esp.get_chip_revision() != 302:
        raise PanelError("requires an unrestricted WiFi6 ESP32-P4 v3.2 downloader")
    if esp.get_crystal_freq() != 40:
        raise PanelError("expected the ESP32-P4 40 MHz crystal")
    bus = Dsi(esp, HOST, time)
    bus.initialize()
    return check_panel(bus, probe_only)


if __name__ == "__main__":
    run_cli(protect, __doc__)
