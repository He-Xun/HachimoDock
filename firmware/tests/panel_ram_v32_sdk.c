/* Host-only oracle. These SDK peripheral objects are ordinary process memory. */
#include <stdio.h>
#include "hal/ldo_ll.h"
#include "soc/clk_tree_defs.h"
#include "soc/hp_sys_clkrst_reg.h"
#include "soc/mipi_dsi_bridge_reg.h"

efuse_dev_t EFUSE;
pmu_dev_t PMU;

uint32_t efuse_hal_blk_version(void) {
    return EFUSE.rd_mac_sys_2.blk_version_minor;
}

uint32_t efuse_hal_chip_revision(void) {
    return 302;
}

int main(void) {
    unsigned version, calibration;
    while (scanf("%u %x", &version, &calibration) == 2) {
        EFUSE.rd_mac_sys_2.blk_version_minor = version;
        EFUSE.rd_mac_sys_3.val = calibration;
        PMU.ext_ldo[1].pmu_ext_ldo.val = 0xa1234000;
        PMU.ext_ldo[1].pmu_ext_ldo_ana.val = 1u << 27;
        int unit = LDO_ID2UNIT(3);
        uint8_t dref, mul;
        bool bypass;
        ldo_ll_voltage_to_dref_mul(unit, 2500, &dref, &mul, &bypass);
        ldo_ll_adjust_voltage(unit, dref, mul, bypass);
        ldo_ll_set_owner(unit, LDO_LL_UNIT_OWNER_SW);
        ldo_ll_enable_ripple_suppression(unit, true);
        ldo_ll_enable(unit, true);
        printf("%08x %08x\n", PMU.ext_ldo[1].pmu_ext_ldo.val,
               PMU.ext_ldo[1].pmu_ext_ldo_ana.val);
    }
}
