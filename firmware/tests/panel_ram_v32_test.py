"""rev302 ROM guard, including an offline comparison with ESP-IDF 5.5.4."""
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import panel_ram_v32 as guard
from panel_ram_v13_test import FakeRom


class Rev302(FakeRom):
    revision = 302

    def __init__(self):
        super().__init__()
        self.trace = []
        # Start with a different PLL mux and divider, keeping unrelated bits set.
        self.regs[guard.PERI_CLK_CTRL03] = guard.PLLREF_FIELDS | 0xB000
        self.regs[guard.VO3_ANA] = 1 << 27
        self.regs[guard.VO3_CTRL] = 0xA1234000

    def get_crystal_freq(self):
        return 40

    def write_reg(self, addr, value):
        self.trace.append((addr, value))
        super().write_reg(addr, value)


def test_rev302_clocks_pll_sleep(monkeypatch):
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)
    rom = Rev302()
    result = guard.protect(rom)
    assert result["confirmed"] and rom.commands == [0x28, 0x10]
    assert rom.regs[guard.PERI_CLK_CTRL03] == 0xB003
    assert [v for a, v in rom.trace if a == guard.BRIDGE_EN] == [2, 0]
    assert [v for a, v in rom.trace if a == guard.HOST + 0xB8] == [
        0x10044, 0x0E, 0x10019, 0x30, 0x10017, 3, 0x10018, 17, 0x10018, 0x81]
    assert {a for a, _ in rom.trace if 0x50100000 <= a} == {guard.VO3_CTRL, guard.VO3_ANA}


@pytest.mark.parametrize("reg", [4, 0xDA, 0xDB, 0xDC])
def test_id_mismatch(rom_v32, reg):
    rom_v32.ids[reg] ^= 1
    with pytest.raises(guard.PanelError, match="ID mismatch"):
        guard.protect(rom_v32)
    assert not rom_v32.commands


@pytest.fixture
def rom_v32(monkeypatch):
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)
    return Rev302()


@pytest.mark.parametrize("rev", [103, 300, 301, 303])
def test_reject_before_mmio(rom_v32, rev):
    rom_v32.revision = rev
    with pytest.raises(guard.PanelError):
        guard.protect(rom_v32)
    assert not rom_v32.trace


def test_probe_only_and_failed_sleep(rom_v32):
    assert guard.protect(rom_v32, probe_only=True)["rdid"] == "304903"
    assert not rom_v32.commands
    rom_v32.modes = [0x9C, 8, 0]
    with pytest.raises(guard.PanelError, match="sleep unconfirmed"):
        guard.protect(rom_v32)


def test_vo3_readback_failure(rom_v32):
    rom_v32.write_reg = lambda addr, val: None
    with pytest.raises(guard.PanelError, match="readback"):
        guard.protect(rom_v32)
    assert not rom_v32.read_commands


def test_sdk_vo3_and_register_map(tmp_path, monkeypatch):
    # Invoke the real 5.5.4 inline functions on in-memory PMU and eFuse structs.
    # Only the revision queries are supplied by the harness.
    default = TOOLS.parents[2] / "toolchains/platformio/v3/packages/framework-espidf"
    sdk = Path(os.environ.get("PET_P4_V3_IDF_PATH", default))
    if not sdk.is_dir():
        pytest.skip("set PET_P4_V3_IDF_PATH for the local ESP-IDF 5.5.4 comparison")
    assert (sdk / "version.txt").read_text().strip() == "5.5.4"
    includes = ["soc/esp32p4/register/hw_ver3", "soc/esp32p4/include", "soc/include",
                "hal/include", "hal/esp32p4/include", "hal/platform_port/include", "esp_common/include"]
    (tmp_path / "sdkconfig.h").write_text("#define CONFIG_IDF_TARGET_ESP32P4 1\n")
    assertions = {
        "DR_REG_DSI_HOST_BASE": guard.HOST, "DSI_BRG_EN_REG": guard.BRIDGE_EN,
        "HP_SYS_CLKRST_SOC_CLK_CTRL1_REG": guard.SOC_CLK_CTRL1,
        "HP_SYS_CLKRST_REF_CLK_CTRL1_REG": guard.REF_CLK_CTRL1,
        "HP_SYS_CLKRST_REF_CLK_CTRL2_REG": guard.REF_CLK_CTRL2,
        "HP_SYS_CLKRST_PERI_CLK_CTRL02_REG": guard.PERI_CLK_CTRL02,
        "HP_SYS_CLKRST_PERI_CLK_CTRL03_REG": guard.PERI_CLK_CTRL03,
        "HP_SYS_CLKRST_HP_RST_EN0_REG": guard.HP_RST_EN0,
        "HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_PLL_REFCLK_SRC_SEL_M | HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_PLL_REFCLK_DIV_NUM_M": guard.PLLREF_FIELDS,
        "DR_REG_PMU_BASE + offsetof(pmu_dev_t, ext_ldo[1].pmu_ext_ldo)": guard.VO3_CTRL,
        "DR_REG_PMU_BASE + offsetof(pmu_dev_t, ext_ldo[1].pmu_ext_ldo_ana)": guard.VO3_ANA,
        "DR_REG_EFUSE_BASE + offsetof(efuse_dev_t, rd_mac_sys_2)": guard.BLOCK_VERSION,
        "DR_REG_EFUSE_BASE + offsetof(efuse_dev_t, rd_mac_sys_3)": guard.VO3_CALIBRATION,
        "MIPI_DSI_PHY_PLLREF_CLK_SRC_DEFAULT == SOC_MOD_CLK_XTAL": 1,
        "MIPI_DSI_PHY_CFG_CLK_SRC_DEFAULT == SOC_MOD_CLK_PLL_F20M": 1,
    }
    harness = Path(__file__).with_name("panel_ram_v32_sdk.c").read_text()
    checks = [f'_Static_assert(({expr}) == {value}u, "{expr}");' for expr, value in assertions.items()]
    exe = tmp_path / "sdk-vo3"
    subprocess.run(["cc", "-std=gnu11", "-O2", f"-I{tmp_path}",
                    *(f"-I{sdk / 'components' / inc}" for inc in includes),
                    "-x", "c", "-", "-o", str(exe)], input=harness + "\n".join(checks),
                   text=True, check=True)
    rng = random.Random(302)
    samples = [(0, 0xFFFFFFFF), (1, 0)] + [(1, rng.getrandbits(32)) for _ in range(128)]
    reference = subprocess.check_output([str(exe)], text=True, input="".join(
        f"{ver} {word:x}\n" for ver, word in samples)).splitlines()
    assert len(reference) == len(samples)
    monkeypatch.setattr(guard.time, "sleep", lambda _: None)
    for (ver, word), line in zip(samples, reference):
        rom = Rev302()
        rom.regs[guard.BLOCK_VERSION] = ver << 8
        rom.regs[guard.VO3_CALIBRATION] = word
        guard.Dsi(rom, guard.HOST, guard.time).enable_vo3()
        assert line == f"{rom.regs[guard.VO3_CTRL]:08x} {rom.regs[guard.VO3_ANA]:08x}"


@pytest.mark.parametrize("modes,accepted", [
    ([0x9C, 0x98, 8, 8], True), ([0x98, 0x98, 8, 8], True), ([8, 8, 8], True),
    ([0xFF], False), ([0], False), ([0x9C, 0x9C], False),
    ([0x9C, 0x98, 8, 0x9C], False),
])
def test_unprogrammed_id_requires_valid_power_readback(rom_v32, modes, accepted):
    rom_v32.ids = {4: 0xFFFFFF, 0xDA: 255, 0xDB: 255, 0xDC: 255}
    rom_v32.modes = modes.copy()
    if accepted:
        result = guard.protect(rom_v32)
        assert result["confirmed"] and result["readback"] == [8, 8]
        assert result["commands"] == ([] if modes[0] == 8 else [0x28, 0x10])
    else:
        from panel_ram_common import PanelIdentityUnknown
        with pytest.raises(guard.PanelError) as exc:
            guard.protect(rom_v32)
        assert isinstance(exc.value, PanelIdentityUnknown) == (modes[0] in (0, 0xFF))
