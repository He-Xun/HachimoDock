"""Portable ST7102 transaction and display ID regressions. Run with python3."""
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

BSP = Path(__file__).resolve().parents[1]
SOURCE = (BSP / "esp32_p4_wifi6_touch_lcd_4_3.c").read_text()
DISPLAY = (BSP / "include/bsp/display.h").read_text()


def run_c(code):
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "fixture"
        subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-x", "c", "-",
                        "-o", str(exe)], input=code, text=True, check=True)
        return subprocess.check_output([str(exe)], timeout=5).decode().splitlines()


class DisplayTests(unittest.TestCase):
    def test_boe_startup_trace(self):
        driver = (BSP / "st7102.c").read_text()
        # All external calls, including the panel constructor, go to the fixture.
        implementation = driver[driver.index("static const char *TAG"):]
        trace = run_c(r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
typedef int esp_err_t, esp_lcd_dpi_panel_config_t;
typedef void *esp_lcd_dsi_bus_handle_t, *esp_lcd_panel_io_handle_t;
typedef struct panel {
    void *user_data;
    esp_err_t (*init)(struct panel *), (*del)(struct panel *), (*reset)(struct panel *);
    esp_err_t (*disp_on_off)(struct panel *, bool);
} esp_lcd_panel_t, *esp_lcd_panel_handle_t;
enum { ESP_OK, ESP_ERR_NO_MEM, LCD_CMD_SWRESET=1, LCD_CMD_DISPON=0x29, LCD_CMD_DISPOFF=0x28 };
#define ESP_RETURN_ON_ERROR(call, tag, ...) do { (void)(tag); int e=(call); if(e) return e; } while(0)
#define pdMS_TO_TICKS(ms) (ms)
static void vTaskDelay(int ms) { printf("wait %d\n", ms); }
static esp_lcd_panel_t dpi;
static int fail_cmd = -1;
static int dpi_init(esp_lcd_panel_t *p) { assert(p == &dpi); puts("dpi"); return 0; }
static int dpi_del(esp_lcd_panel_t *p) { assert(p == &dpi); puts("del"); return 0; }
static int esp_lcd_new_panel_dpi(void *b, const int *c, esp_lcd_panel_handle_t *out) {
    assert(b && c); dpi.init=dpi_init; dpi.del=dpi_del; *out=&dpi; return ESP_OK;
}
static int esp_lcd_panel_io_tx_param(void *io, int cmd, const void *data, size_t size) {
    assert(io == &dpi);
    printf("%02x %zu", cmd, size);
    for(size_t i=0;i<size;i++) printf(" %02x", ((const uint8_t *)data)[i]);
    puts("");
    return cmd == fail_cmd ? -1 : ESP_OK;
}
""" + implementation + r"""
int main(void) {
    int config=0;
    esp_lcd_panel_handle_t p=NULL;
    assert(bsp_new_st7102_panel(&config,&dpi,&config,&p)==ESP_OK);
    assert(p->reset(p)==ESP_OK && p->init(p)==ESP_OK);
    assert(p->disp_on_off(p,true)==ESP_OK && p->disp_on_off(p,false)==ESP_OK);
    fail_cmd=0x3a;
    assert(p->init(p)==-1 && p->del(p)==ESP_OK);
}
""")
        self.assertEqual(trace[:5], ["01 0", "wait 120", "f0 1 00", "36 1 00", "3a 1 55"])
        self.assertEqual(trace[-11:], ["11 0", "wait 120", "29 0", "wait 20", "dpi",
                                       "29 0", "28 0", "f0 1 00", "36 1 00", "3a 1 55", "del"])
        # Fingerprint of main/main.c lcd_init_cmds from the supplier BOE zip.
        # Each record is command, length, delay in ms, then transmitted bytes.
        records = []
        for line in trace[5:trace.index("dpi")]:
            command, value, *data = line.split()
            if command == "wait":
                records[-1][2] = int(value)
            else:
                records.append([int(command, 16), int(value), 0, *bytes.fromhex(" ".join(data))])
        stream = b"".join(bytes(record) for record in records)
        self.assertEqual(hashlib.sha256(stream).hexdigest(),
                         "b2ec2a6b25a3eb1b4819ea8ba5806d4e6a3c874d27c925523c721ffb565a0462")

    def test_boe_scanout_configuration(self):
        # Keep the diagnostic constants in sync with the actual bus and DPI setup.
        for name, value in (("LANES", 2), ("LANE_MBPS", 600), ("CLOCK_MHZ", 40)):
            self.assertRegex(DISPLAY, rf"#define BSP_LCD_ST7102_{name} {value}\b")
        bus, dpi = re.findall(r"if \(st7102\) \{([^}]+)\}", SOURCE)
        self.assertIn("bus_config.num_data_lanes = BSP_LCD_ST7102_LANES;", bus)
        self.assertIn("bus_config.lane_bit_rate_mbps = BSP_LCD_ST7102_LANE_MBPS;", bus)
        self.assertIn("dpi_config.dpi_clock_freq_mhz = BSP_LCD_ST7102_CLOCK_MHZ;", dpi)
        self.assertIn("dpi_config.pixel_format = LCD_COLOR_PIXEL_FORMAT_RGB565;", dpi)
        self.assertIn("dpi_config.num_fbs = 1;", dpi)
        for axis, timing in (("h", (40, 2, 40)), ("v", (9, 2, 180))):
            for field, value in zip(("back_porch", "pulse_width", "front_porch"), timing):
                self.assertIn(f"video_timing.{axis}sync_{field} = {value};", dpi)

    def test_bounded_probe(self):
        reader = SOURCE[SOURCE.index("static int read_display_register"):SOURCE.index("esp_err_t bsp_nv3049_sleep")]
        probe = SOURCE[SOURCE.index("static bool matches_nv3049_id"):SOURCE.index("esp_err_t bsp_display_new(")]
        enum = re.search(r"typedef enum \{[^}]+\} bsp_display_id_t;", DISPLAY)[0]
        trace = run_c(r"""
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <stdio.h>
#define ESP_LOGW(tag, format, ...) printf(format "\n", __VA_ARGS__)
#define BSP_LCD_PANEL_AUTO 1
#define BSP_LCD_NV3049_LANES 2
#define BSP_LCD_NV3049_LANE_MBPS 500
enum { ESP_OK, MIPI_DSI_DT_SET_MAXIMUM_RETURN_PKT, MIPI_DSI_DT_DCS_READ_0 };
typedef int dsi_host_dev_t, *esp_lcd_dsi_bus_handle_t;
typedef struct {int bus_id,num_data_lanes,phy_clk_src,lane_bit_rate_mbps;} esp_lcd_dsi_bus_config_t;
static int mock_host, rdid, individual, pending, value, reads, delays, deleted, busy;
#define MIPI_DSI_LL_GET_HOST(i) (&mock_host)
#define mipi_dsi_host_ll_enable_cmd_ack(...)
#define mipi_dsi_host_ll_set_clock_lane_state(...)
#define mipi_dsi_host_ll_set_dcs_short_rd_speed_mode(...)
#define mipi_dsi_host_ll_set_mrps_speed_mode(...)
#define mipi_dsi_host_ll_enable_bta(...)
#define mipi_dsi_host_ll_gen_set_rx_vcid(...)
#define vTaskDelay(...)
static int bsp_enable_dsi_phy_power(void) {return ESP_OK;}
static int esp_lcd_new_dsi_bus(const esp_lcd_dsi_bus_config_t *cfg, int **bus) {
    assert(cfg->bus_id == 0); *bus=&mock_host; return ESP_OK;
}
static void esp_lcd_del_dsi_bus(int *bus) {assert(bus == &mock_host); ++deleted;}
static void esp_rom_delay_us(int us) {assert(us==20 && ++delays<=3000);}
static bool mipi_dsi_host_ll_gen_is_read_cmd_busy(int *h) {return busy;}
static bool mipi_dsi_host_ll_gen_is_cmd_fifo_empty(int *h) {return true;}
static bool mipi_dsi_host_ll_gen_is_read_fifo_empty(int *h) {return !pending;}
static bool mipi_dsi_host_ll_gen_is_cmd_fifo_full(int *h) {return false;}
static uint32_t mipi_dsi_host_ll_gen_read_payload_fifo(int *h) {pending=0;return value;}
static void mipi_dsi_host_ll_gen_set_packet_header(int *h,int vc,int kind,int a,int reg) {
    if(kind!=MIPI_DSI_DT_DCS_READ_0) return;
    ++reads;
    switch(reg) {
    case 0x04: value=rdid; break;
    case 0xda: value=individual & 0xff; break;
    case 0xdb: value=(individual >> 8) & 0xff; break;
    case 0xdc: value=individual >> 16; break;
    default: abort();
    }
    pending=value>=0;
}
""" + enum +
              "\nbsp_display_id_t bsp_probe_display_id(void);\n#if BSP_LCD_PANEL_AUTO\n" + reader + probe + r"""
int main(void) {
    int unknown[]={0,0xffffff,0x120271};
    for(size_t i=0;i<sizeof(unknown)/sizeof(unknown[0]);++i) {
        reads=delays=deleted=0; rdid=individual=unknown[i];
        assert(bsp_probe_display_id()==BSP_DISPLAY_ID_UNKNOWN);
        assert(reads==(rdid==0x120271 ? 4 : 1) && deleted==1 && delays==0);
    }
    reads=deleted=0; rdid=-1;
    assert(bsp_probe_display_id()==BSP_DISPLAY_ID_UNKNOWN && reads==2 && deleted==1 && delays==3000);
    reads=delays=deleted=0; busy=1;
    assert(bsp_probe_display_id()==BSP_DISPLAY_ID_UNKNOWN && reads==0 && deleted==1 && delays==3000);
    busy=0;rdid=individual=0x034930;reads=deleted=0;
    assert(bsp_probe_display_id()==BSP_DISPLAY_ID_NV3049 && reads==4 && deleted==1);
    assert(bsp_probe_nv3049_display());
    rdid=individual=0xfba080; reads=deleted=0;
    assert(bsp_probe_display_id()==BSP_DISPLAY_ID_ST7102 && reads==4 && deleted==1);
    individual=0xfba081; reads=deleted=0;
    assert(bsp_probe_display_id()==BSP_DISPLAY_ID_UNKNOWN && reads==8 && deleted==1);
}
""")
        self.assertEqual(trace[:3], [
            "DCS ID attempt 1 04=[00 00 00] reserved",
            "DCS ID attempt 1 04=[FF FF FF] reserved",
            "DCS ID attempt 1 04=[71 02 12] DA=71 DB=02 DC=12 consistent=yes",
        ])
        self.assertEqual(trace[3:7], ["DCS ID attempt 1 04 unavailable",
                                      "DCS ID attempt 2 04 unavailable"] * 2)


if __name__ == "__main__":
    unittest.main()
