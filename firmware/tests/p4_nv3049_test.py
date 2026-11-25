"""Test the NV3049 scanout and CST328 with portable panel and bus fixtures."""
from hashlib import sha256
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def compile_and_run(binary, sources, includes):
    subprocess.run(
        ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
         *['-I' + str(path) for path in includes],
         *[str(ROOT / source) for source in sources], '-o', str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


def test_rotated_rgb888_and_touch_coordinates(tmp_path):
    compile_and_run(
        tmp_path / 'nv3049-pixels',
        ['tests/nv3049_pixels_test.c', 'main/pet_p4_touch_core.c'],
        [ROOT / 'main'],
    )


def test_cst328_bus_and_reports(tmp_path):
    for header in ('esp_err.h', 'esp_log.h', 'driver/i2c_master.h',
                   'freertos/FreeRTOS.h', 'freertos/task.h'):
        path = tmp_path / 'mock' / header
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "cst328_host_stubs.h"\n')
    compile_and_run(
        tmp_path / 'cst328',
        ['tests/cst328_driver_test.c', 'main/pet_p4_cst328.c'],
        [ROOT / 'main', ROOT / 'tests', tmp_path / 'mock'],
    )


def test_panel_selection_requires_a_verified_controller(tmp_path):
    touch = (ROOT / 'main/pet_p4_touch.c').read_text()
    entry = 'bool pet_p4_touch_try_nv3049(void) {'
    detector = entry + touch.split(entry, 1)[1].split('\n}\n', 1)[0] + '\n}\n'
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>

typedef int esp_err_t;
typedef void *i2c_master_bus_handle_t;
enum { ESP_OK, ESP_ERR_INVALID_STATE, ESP_ERR_NOT_FOUND, ESP_ERR_NOT_SUPPORTED, ESP_ERR_TIMEOUT };
enum { PET_CST328_ADDRESS = 0x1a };
static int g_address, g_cst328_info;
static bool bus_available;
static esp_err_t init_result, probe_result, cst_result;
static unsigned probes, cst_calls, closed;
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
static int bsp_i2c_init(void) { return init_result; }
static void *bsp_i2c_get_handle(void) { return bus_available ? &g_address : NULL; }
static int i2c_master_probe(void *bus, int address, int timeout) {
    assert(bus == &g_address && address == PET_CST328_ADDRESS && timeout == 20);
    probes++;
    return probe_result;
}
static int pet_cst328_init(void *bus, int *info) {
    assert(bus == &g_address && info == &g_cst328_info);
    cst_calls++;
    return cst_result;
}
static void pet_cst328_deinit(void) { closed++; }
'''
    scenarios = r'''
static void reset(int init, bool bus, int probe, int cst) {
    init_result = init;
    bus_available = bus;
    probe_result = probe;
    cst_result = cst;
    probes = cst_calls = closed = 0;
    g_address = 0;
}
int main(void) {
    reset(ESP_OK, true, ESP_OK, ESP_OK);
    assert(pet_p4_touch_try_nv3049() && g_address == PET_CST328_ADDRESS);
    assert(probes == 1 && cst_calls == 1 && closed == 0);

    // A legacy module without touch still takes the legacy display path.
    reset(ESP_OK, true, ESP_ERR_NOT_FOUND, ESP_OK);
    assert(!pet_p4_touch_try_nv3049() && g_address == 0);
    assert(probes == 1 && cst_calls == 0 && closed == 0);

    reset(ESP_OK, true, ESP_ERR_TIMEOUT, ESP_OK);
    assert(!pet_p4_touch_try_nv3049() && probes == 1 && cst_calls == 0);

    // An I2C address ACK alone cannot select NV3049.
    reset(ESP_OK, true, ESP_OK, ESP_ERR_NOT_SUPPORTED);
    assert(!pet_p4_touch_try_nv3049() && g_address == 0);
    assert(probes == 1 && cst_calls == 1 && closed == 1);

    reset(ESP_ERR_INVALID_STATE, true, ESP_OK, ESP_OK);
    assert(!pet_p4_touch_try_nv3049() && probes == 0 && cst_calls == 0);
    reset(ESP_OK, false, ESP_OK, ESP_OK);
    assert(!pet_p4_touch_try_nv3049() && probes == 0 && cst_calls == 0);
}
'''
    source = tmp_path / 'panel_selection.c'
    source.write_text(harness + detector + scenarios)
    binary = tmp_path / 'panel-selection'
    subprocess.run(
        ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)


def test_nv3049_paths_share_init_trace_and_st7701_is_isolated(tmp_path):
    bsp_dir = ROOT / 'components/esp32_p4_wifi6_touch_lcd_4_3'
    bsp = (bsp_dir / 'esp32_p4_wifi6_touch_lcd_4_3.c').read_text()
    display = (bsp_dir / 'include/bsp/display.h').read_text()
    lcd = (ROOT / 'main/pet_p4_lcd.c').read_text()

    # Keep the production controller, config type, and both selection gates.
    controller_start = '#if BSP_LCD_PANEL_AUTO\n#include "nv3049_init.h"'
    controller = bsp[bsp.index(controller_start):].split('\n#endif', 1)[0] + '\n#endif\n'
    config = re.search(r'typedef struct \{[^{}]*\}\s*bsp_display_config_t;', display)[0]
    constructor = bsp.split('esp_err_t bsp_display_new_with_handles(', 1)[1]
    selector = '#if BSP_LCD_PANEL_AUTO\n' + constructor.split('#if BSP_LCD_PANEL_AUTO\n', 1)[1]
    selector = selector.split('\n#endif', 1)[0] + '\n#endif\n'
    panel_start = '#if BSP_LCD_PANEL_AUTO\n    if (nv3049) {\n        dpi_config.'
    panel_branch = constructor[constructor.index(panel_start):].split(
        '        ESP_LOGI(TAG, "Install Waveshare', 1,
    )[0]
    lcd_start = 'esp_err_t pet_p4_lcd_init(void) {'
    lcd_selection = lcd[lcd.index(lcd_start):].split('  if (!g_refresh_done_sem)', 1)[0]

    prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdatomic.h>
#include <string.h>

typedef int esp_err_t;
typedef int *esp_lcd_panel_io_handle_t;
enum { ESP_OK, ESP_ERR_INVALID_STATE, PET_LCD_PANEL_ST7701, PET_LCD_PANEL_NV3049, PET_LCD_PANEL_ST7102 };
typedef int pet_lcd_panel_model_t;
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define ESP_RETURN_ON_ERROR(call, ...) do { int result = (call); if (result) return result; } while (0)
#define ESP_RETURN_ON_FALSE(ok, error, ...) do { if (!(ok)) return error; } while (0)

static int bus_io;
#if BSP_LCD_PANEL_AUTO
enum { ESP_RST_POWERON = 1 };
static int test_reset_reason;
static int esp_reset_reason(void) { return test_reset_reason; }
static esp_err_t bsp_nv3049_sleep_for_flash(esp_lcd_panel_io_handle_t io, bool keep) {
    assert(!keep);
    assert(io == &bus_io);
    puts("cold panel sleep");
    return ESP_OK;
}
#endif
static int panel_storage;
static int *g_panel_handle, *g_lcd_io;
static unsigned profile_reads, profile_writes, touch_probes;
#if BSP_LCD_PANEL_AUTO
typedef enum { PET_LCD_PROFILE_AUTO, PET_LCD_PROFILE_NV3049, PET_LCD_PROFILE_ST7102 } pet_lcd_profile_t;
static pet_lcd_profile_t saved_profile;
static bool verified_touch, profile_error;
static atomic_int g_configured_profile;
static _Atomic(const char *) g_selection;
typedef enum { BSP_DISPLAY_ID_UNKNOWN, BSP_DISPLAY_ID_NV3049, BSP_DISPLAY_ID_ST7102, BSP_DISPLAY_ID_ST7701 } bsp_display_id_t;
static bsp_display_id_t display_id;
static bool sitronix_touch;
static bool pet_p4_touch_try_st7123(void) { return sitronix_touch; }
static bool display_id_matches;
static bsp_display_id_t bsp_probe_display_id(void) { return display_id_matches ? BSP_DISPLAY_ID_NV3049 : display_id; }
static atomic_bool g_no_touch_profile;
static atomic_int g_panel_model;

static esp_err_t load_lcd_profile(pet_lcd_profile_t *profile) {
    ++profile_reads;
    if (profile_error) return ESP_ERR_INVALID_STATE;
    *profile = saved_profile;
    return ESP_OK;
}

static esp_err_t pet_p4_lcd_save_profile(pet_lcd_profile_t profile) {
    assert(profile == PET_LCD_PROFILE_NV3049 && verified_touch);
    ++profile_writes;
    saved_profile = profile;
    return ESP_OK;
}
static bool pet_p4_touch_try_nv3049(void) {
    ++touch_probes;
    return verified_touch;
}

static int xPortGetCoreID(void) { return 0; }

enum {
    BSP_LCD_RST = 17, GPIO_MODE_OUTPUT = 1,
    MIPI_DSI_LL_CLOCK_LANE_STATE_HS = 3,
    BSP_LCD_NV3049_CLOCK_MHZ = 24, LCD_COLOR_PIXEL_FORMAT_RGB888 = 24
};
#define pdMS_TO_TICKS(ms) (ms)
#define MIPI_DSI_LL_GET_HOST(bus) (bus)
typedef struct { uint64_t pin_bit_mask; int mode; } gpio_config_t;
typedef struct {
    unsigned dpi_clock_freq_mhz, pixel_format, num_fbs;
    struct { bool use_dma2d; } flags;
    struct {
        unsigned hsync_back_porch, hsync_pulse_width, hsync_front_porch;
        unsigned vsync_back_porch, vsync_pulse_width, vsync_front_porch;
    } video_timing;
} esp_lcd_dpi_panel_config_t;

static esp_err_t gpio_config(const gpio_config_t *config) {
    assert(config->pin_bit_mask == (1ULL << BSP_LCD_RST) && config->mode == GPIO_MODE_OUTPUT);
    puts("reset output");
    return ESP_OK;
}

static esp_err_t gpio_set_level(int pin, int level) {
    assert(pin == BSP_LCD_RST);
    printf("reset %d\n", level);
    return ESP_OK;
}

static void vTaskDelay(unsigned ms) { printf("wait %u\n", ms); }

static void mipi_dsi_host_ll_set_clock_lane_state(int host, int mode) {
    assert(mode == MIPI_DSI_LL_CLOCK_LANE_STATE_HS);
    printf("clock %d HS\n", host);
}

static esp_err_t esp_lcd_panel_io_tx_param(esp_lcd_panel_io_handle_t io, int command,
                                         const void *payload, size_t bytes) {
    assert(io == &bus_io && bytes <= 1 && (payload != NULL) == (bytes != 0));
    if (bytes) {
        printf("dcs %02x 1 %02x\n", (unsigned)command, (unsigned)*(const uint8_t *)payload);
    } else {
        printf("dcs %02x 0\n", (unsigned)command);
    }
    return ESP_OK;
}

static esp_err_t esp_lcd_new_panel_dpi(int bus, const esp_lcd_dpi_panel_config_t *config,
                                      int **panel) {
    assert(bus == 0 && config->pixel_format == LCD_COLOR_PIXEL_FORMAT_RGB888);
    *panel = &panel_storage;
    puts("NV3049");
    return ESP_OK;
}

static esp_err_t esp_lcd_panel_init(int *panel) {
    assert(panel == &panel_storage);
    return ESP_OK;
}
#endif
'''
    bsp_stub_start = r'''
static esp_err_t bsp_display_new(const bsp_display_config_t *config, int **panel, int **out_io) {
#if BSP_LCD_PANEL_AUTO
    esp_err_t ret = ESP_OK;
    esp_lcd_panel_io_handle_t io = &bus_io;
    int mipi_dsi_bus = 0;
    int *panel_handle;
    esp_lcd_dpi_panel_config_t dpi_config = {0};
#define ESP_GOTO_ON_ERROR(call, label, ...) do { ret = (call); if (ret) goto label; } while (0)
#else
    assert(!config->nv3049);
#endif
'''
    bsp_stub_end = r'''
        puts(config->st7102 ? "ST7102" : "ST7701");
    }
    *panel = &panel_storage;
    *out_io = &bus_io;
    return ESP_OK;
#if BSP_LCD_PANEL_AUTO
err:
    return ret;
#endif
}
'''
    scenarios = r'''
#if BSP_LCD_PANEL_AUTO
static void run_case(const char *name, pet_lcd_profile_t profile, bool touch, bool fail_profile, int model) {
    puts(name);
    saved_profile = profile;
    verified_touch = touch;
    profile_error = fail_profile;
    g_panel_handle = NULL;
    profile_reads = profile_writes = touch_probes = 0;
    assert(pet_p4_lcd_init() == ESP_OK);
    assert(profile_reads == 1 && touch_probes == 1);
    assert(profile_writes == (unsigned)(!fail_profile && profile == PET_LCD_PROFILE_AUTO && !display_id_matches && !display_id && !sitronix_touch && touch));
    assert(atomic_load(&g_configured_profile) == (int)(fail_profile ? PET_LCD_PROFILE_AUTO : profile));
    assert(atomic_load(&g_no_touch_profile) == (model == PET_LCD_PANEL_NV3049 && !touch));
    assert(atomic_load(&g_panel_model) == model);
}
#endif

int main(void) {
#if BSP_LCD_PANEL_AUTO
    run_case("saved NV3049 profile", PET_LCD_PROFILE_NV3049, false, false, PET_LCD_PANEL_NV3049);
    run_case("verified touch", PET_LCD_PROFILE_AUTO, true, false, PET_LCD_PANEL_NV3049);
    assert(saved_profile == PET_LCD_PROFILE_NV3049);
    run_case("remembered display after unplug", saved_profile, false, false, PET_LCD_PANEL_NV3049);
    run_case("unverified touch", PET_LCD_PROFILE_AUTO, false, false, PET_LCD_PANEL_ST7701);
    run_case("unreadable profile", PET_LCD_PROFILE_AUTO, true, true, PET_LCD_PANEL_NV3049);
    run_case("saved NV3049 with touch", PET_LCD_PROFILE_NV3049, true, false, PET_LCD_PANEL_NV3049);
    display_id_matches = true;
    run_case("blank board without touch", PET_LCD_PROFILE_AUTO, false, false, PET_LCD_PANEL_NV3049);
    assert(!strcmp(atomic_load(&g_selection), "verified-panel-id"));
    test_reset_reason = ESP_RST_POWERON;
    run_case("cold NV", PET_LCD_PROFILE_AUTO, false, false, PET_LCD_PANEL_NV3049);
    display_id_matches = false;
    run_case("cold legacy", PET_LCD_PROFILE_AUTO, false, false, PET_LCD_PANEL_ST7701);
    sitronix_touch = true;
    run_case("ST7123 replaces saved NV", PET_LCD_PROFILE_NV3049, false, false, PET_LCD_PANEL_ST7102);
    assert(!strcmp(atomic_load(&g_selection), "verified-touch"));
    run_case("ambiguous touch", PET_LCD_PROFILE_AUTO, true, false, PET_LCD_PANEL_ST7701);
    assert(!strcmp(atomic_load(&g_selection), "legacy-default"));
    display_id = BSP_DISPLAY_ID_ST7701;
    run_case("positive old ID wins", PET_LCD_PROFILE_ST7102, false, false, PET_LCD_PANEL_ST7701);
    display_id = BSP_DISPLAY_ID_ST7102;
    sitronix_touch = false;
    run_case("positive new ID wins", PET_LCD_PROFILE_NV3049, true, false, PET_LCD_PANEL_ST7102);
    assert(!strcmp(atomic_load(&g_selection), "verified-panel-id"));
    display_id = BSP_DISPLAY_ID_UNKNOWN;
    run_case("saved ST7102", PET_LCD_PROFILE_ST7102, false, false, PET_LCD_PANEL_ST7102);
    assert(!strcmp(atomic_load(&g_selection), "configured"));
#else
    assert(pet_p4_lcd_init() == ESP_OK);
    assert(!profile_reads && !touch_probes && !profile_writes);
#endif
    return 0;
}
'''
    source = tmp_path / 'nv3049-init.c'
    source.write_text('\n'.join((
        prelude, config, controller, bsp_stub_start, selector, panel_branch, bsp_stub_end,
        lcd_selection, '  return ESP_OK;\n}', scenarios,
    )))

    traces = {}
    for auto in (0, 1):
        binary = tmp_path / f'nv3049-init-{auto}'
        subprocess.run(
            ['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
             f'-DBSP_LCD_PANEL_AUTO={auto}', '-I', str(bsp_dir / 'priv_include'),
             str(source), '-o', str(binary)],
            check=True,
        )
        traces[auto] = subprocess.run(
            [str(binary)], check=True, capture_output=True, text=True,
        ).stdout.splitlines()

    assert traces[0] == ['ST7701']
    events = traces[1]
    assert events[0] == 'saved NV3049 profile'
    touch, legacy, failed_profile = map(
        events.index, ('verified touch', 'unverified touch', 'unreadable profile'),
    )
    remembered = events.index('remembered display after unplug')
    verified = events[touch + 1:remembered]
    assert events[remembered + 1:legacy] == verified
    assert events[legacy + 1:failed_profile] == ['ST7701']

    # The BSP's 20 ms pre-bus wait precedes the controller reset pulses.
    reset = ['wait 20', 'reset output', 'reset 1', 'wait 10',
             'reset 0', 'wait 20', 'reset 1', 'wait 120']
    prefix = [*reset, 'clock 0 HS', 'wait 30', 'dcs 11 0', 'wait 220']
    suffix = ['dcs 29 0', 'wait 50', 'NV3049']
    assert verified[:len(prefix)] == prefix
    assert verified[-len(suffix):] == suffix

    vendor = verified[len(prefix):-len(suffix)]
    assert vendor[-4:] == ['dcs 11 1 00', 'wait 220', 'dcs 29 1 00', 'wait 50']
    # Pin the accepted vendor commands, payload bytes, order, repeats and waits.
    assert len(vendor) == 191
    assert sha256('\n'.join(vendor).encode()).hexdigest() == (
        '59c7bc41c1520a321cd47915ae8b077c03ae3c84c2f68d516e90ad8e52b3d82b'
    )
    assert events[1:touch] == verified
    for name in ('saved NV3049 with touch', 'blank board without touch'):
        next_case = events.index(name)
        assert events[failed_profile + 1:next_case] == verified
        failed_profile = next_case
    cold = events.index('cold NV')
    legacy_cold = events.index('cold legacy')
    assert events[failed_profile + 1:cold] == verified
    assert events[cold + 1:legacy_cold] == [*verified[:-1], 'cold panel sleep', *verified[1:]]
    assert events[legacy_cold + 1:] == ['ST7701', 'ST7123 replaces saved NV', 'ST7102',
        'ambiguous touch', 'ST7701', 'positive old ID wins', 'ST7701',
        'positive new ID wins', 'ST7102', 'saved ST7102', 'ST7102']


def test_display_id_requires_matching_independent_reads(tmp_path):
    bsp = (ROOT / 'components/esp32_p4_wifi6_touch_lcd_4_3/esp32_p4_wifi6_touch_lcd_4_3.c').read_text()
    start = bsp.index('static bool matches_nv3049_id(')
    selector = bsp[start:bsp.index('\nbool bsp_probe_nv3049_display(', start)]
    harness = tmp_path / 'display_id.c'
    harness.write_text(r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <assert.h>
typedef int dsi_host_dev_t;
typedef enum { BSP_DISPLAY_ID_UNKNOWN, BSP_DISPLAY_ID_NV3049, BSP_DISPLAY_ID_ST7102 } bsp_display_id_t;
#define ESP_LOGW(tag, ...) ((void)0)
#define pdMS_TO_TICKS(ms) (ms)
static int reads, waits, next_register, response[8];
static void vTaskDelay(unsigned ms) { assert(ms == 30); ++waits; }
static bool mipi_dsi_host_ll_gen_is_read_cmd_busy(int *host) { return *host != 0; }
static bool mipi_dsi_host_ll_gen_is_cmd_fifo_empty(int *host) { return *host == 0; }
static void esp_rom_delay_us(int us) { (void)us; assert(0); }
static int read_display_register(dsi_host_dev_t *host, uint8_t reg, unsigned size) {
    const uint8_t expected[] = {0x04, 0xda, 0xdb, 0xdc};
    assert(*host == 0 && reads < 8 && reg == expected[next_register]);
    assert(size == (next_register == 0 ? 3U : 1U));
    int value = response[4 * waits + next_register];
    ++reads;
    next_register = (reg == 0x04 && value < 0) ? 0 : (next_register + 1) % 4;
    return value;
}
''' + selector + r'''
int main(void) {
    dsi_host_dev_t host = 0;
    const int valid[] = {0x034930, 0x30, 0x49, 0x03};
    for (int i = 0; i < 8; ++i) response[i] = valid[i % 4];
    assert(probe_display_registers(&host) == BSP_DISPLAY_ID_NV3049 && reads == 4 && waits == 0);
    for (int i = 0; i < 4; ++i) {
        const int failures[] = {-1, 0x7f};
        for (unsigned j = 0; j < sizeof(failures)/sizeof(failures[0]); ++j) {
            response[i] = failures[j];
            reads = waits = 0;
            int first_reads = (i == 0 && failures[j] < 0) ? 1 : 4;
            assert(probe_display_registers(&host) == BSP_DISPLAY_ID_NV3049 && reads == first_reads + 4 && waits == 1);
            // Different failures in each tuple must not combine into a match.
            const int other = (i + 1) % 4;
            response[4 + other] = -1;
            reads = waits = 0;
            int second_reads = other == 0 ? 1 : 4;
            assert(probe_display_registers(&host) == BSP_DISPLAY_ID_UNKNOWN && reads == first_reads + second_reads && waits == 1);
            response[4 + other] = valid[other];
        }
        response[i] = valid[i];
    }
}
''')
    compile_and_run(tmp_path / 'display_id', [harness], [])


def test_factory_flash_sleep_checks_both_panel_replies(tmp_path):
    src = (ROOT / 'components/esp32_p4_wifi6_touch_lcd_4_3/esp32_p4_wifi6_touch_lcd_4_3.c').read_text()
    start = src.index('esp_err_t bsp_nv3049_sleep_for_flash(')
    code = src[start:src.index('static bool matches_nv3049_id(', start)]
    harness = tmp_path / 'sleep.c'
    harness.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
typedef int esp_err_t;
typedef int *esp_lcd_panel_io_handle_t;
typedef struct {uint32_t val;} reg_t;
typedef struct {reg_t mode_cfg,pckhdl_cfg,gen_vcid;} dsi_host_dev_t;
static dsi_host_dev_t dsi;
#define MIPI_DSI_LL_GET_HOST(n) (&dsi)
enum {ESP_OK,ESP_ERR_TIMEOUT,ESP_ERR_INVALID_RESPONSE};
#define pdMS_TO_TICKS(n) (n)
static int elapsed, probes, commands, reply[2], fifo_full, keep_video, stuck;
static void vTaskDelay(int ms) {elapsed+=ms;}
static void mipi_dsi_host_ll_enable_video_mode(dsi_host_dev_t *h,bool v) {assert(!v); h->mode_cfg.val=99;}
static void mipi_dsi_host_ll_enable_bta(dsi_host_dev_t *h,bool v) {assert(v);h->pckhdl_cfg.val=8;}
static void mipi_dsi_host_ll_gen_set_rx_vcid(dsi_host_dev_t *h,int n) {h->gen_vcid.val=n;}
static bool mipi_dsi_host_ll_gen_is_cmd_fifo_empty(dsi_host_dev_t *h) {(void)h;return !stuck;}
static bool mipi_dsi_host_ll_gen_is_cmd_fifo_full(dsi_host_dev_t *h) {(void)h;return fifo_full;}
static int esp_lcd_panel_io_tx_param(esp_lcd_panel_io_handle_t io,int cmd,void *arg,int size) {
    assert(io && !arg && !size && commands<2);
    assert(dsi.mode_cfg.val == (keep_video ? 1u : 99u));
    assert((commands==0 && cmd==0x28 && !elapsed) || (commands==1 && cmd==0x10 && elapsed>=50));
    ++commands;return ESP_OK;
}
static int read_display_register(dsi_host_dev_t *h,int reg,int bytes) {
    assert(h->mode_cfg.val==99 && h==&dsi && commands==2 && reg==0x0a && bytes==1 && probes<2);
    assert(elapsed >= 300+probes*100);
    return reply[probes++];
}
''' + code + r'''
int main(void) {
    const struct { int first, second; bool full, pending; } cases[] = {
        {8, 8, false, false}, {0, 8, false, false},
        {8, 0x9c, false, false}, {8, -1, false, false}, {-1, 8, false, false},
        {8, 8, true, false}, {8, 8, false, true},
    };
    for (int keep=0; keep<=1; ++keep) for (size_t i=0; i<sizeof cases / sizeof cases[0]; ++i) {
        elapsed=probes=commands=0;
        fifo_full=cases[i].full; stuck=cases[i].pending; keep_video=keep;
        reply[0]=cases[i].first; reply[1]=cases[i].second;
        dsi=(dsi_host_dev_t){{1},{2},{3}};
        const bool can_read = !fifo_full && !(keep && stuck);
        const bool success = can_read && reply[0]==8 && reply[1]==8;
        assert((bsp_nv3049_sleep_for_flash(&keep,keep)==ESP_OK)==success);
        assert(dsi.mode_cfg.val==(keep && success ? 99u : 1u) && dsi.pckhdl_cfg.val==2 && dsi.gen_vcid.val==3);
        assert(commands == (fifo_full ? 0 : 2));
        assert(probes == (!can_read ? 0 : reply[0]==8 ? 2 : 1));
    }
}
''')
    compile_and_run(tmp_path / 'sleep', [harness], [])


def test_profile_uses_only_new_nvs_key_and_supported_values(tmp_path):
    lcd = (ROOT / "main/pet_p4_lcd.c").read_text()
    header = (ROOT / "main/pet_p4_lcd.h").read_text()
    enum = re.search(r"typedef enum \{\s*PET_LCD_PROFILE_AUTO[^}]*\} pet_lcd_profile_t;", header)[0]
    storage = lcd[lcd.index("static const char *LCD_NVS_NAMESPACE"):lcd.index("bool pet_p4_lcd_no_touch_profile")]
    source = tmp_path / "profile.c"
    source.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
typedef int esp_err_t, nvs_handle_t;
enum {ESP_OK, ESP_ERR_NVS_NOT_FOUND, ESP_ERR_INVALID_STATE, ESP_ERR_INVALID_ARG, NVS_READONLY, NVS_READWRITE};
static int open_result, read_result, opens, reads, writes, commits, closes;
static uint8_t stored;
static esp_err_t nvs_open(const char *ns, int mode, nvs_handle_t *handle) {
    assert(strcmp(ns, "pet_lcd") == 0);
    ++opens; *handle = mode; return open_result;
}
static esp_err_t nvs_get_u8(nvs_handle_t h, const char *key, uint8_t *value) {
    assert(h == NVS_READONLY && strcmp(key, "profile") == 0);
    ++reads;
    if (read_result == ESP_OK) *value = stored;
    return read_result;
}
static esp_err_t nvs_set_u8(nvs_handle_t h, const char *key, uint8_t value) {
    assert(h == NVS_READWRITE && strcmp(key, "profile") == 0);
    ++writes; stored = value; return ESP_OK;
}
static esp_err_t nvs_commit(nvs_handle_t h) { assert(h == NVS_READWRITE); ++commits; return ESP_OK; }
static void nvs_close(nvs_handle_t h) { (void)h; ++closes; }
''' + enum + storage + r'''
int main(void) {
    _Static_assert(PET_LCD_PROFILE_AUTO == 0 && PET_LCD_PROFILE_NV3049 == 1, "NVS encoding");
    pet_lcd_profile_t profile = PET_LCD_PROFILE_NV3049;
    open_result = ESP_ERR_NVS_NOT_FOUND;
    assert(load_lcd_profile(&profile) == ESP_OK && profile == PET_LCD_PROFILE_AUTO && !reads && !closes);
    open_result = ESP_OK; read_result = ESP_ERR_NVS_NOT_FOUND;
    assert(load_lcd_profile(&profile) == ESP_OK && profile == PET_LCD_PROFILE_AUTO && reads == 1);
    read_result = ESP_OK;
    for (unsigned i = 0; i <= 2; ++i) {
        assert(pet_p4_lcd_save_profile((pet_lcd_profile_t)i) == ESP_OK && stored == i);
        assert(load_lcd_profile(&profile) == ESP_OK && (unsigned)profile == i);
    }
    stored = 3;
    assert(load_lcd_profile(&profile) == ESP_ERR_INVALID_STATE);
    assert(pet_p4_lcd_save_profile((pet_lcd_profile_t)3) == ESP_ERR_INVALID_ARG);
    assert(pet_p4_lcd_save_profile((pet_lcd_profile_t)-1) == ESP_ERR_INVALID_ARG);
    assert(opens == 9 && closes == 8 && reads == 5 && writes == 3 && commits == 3);
}
''')
    compile_and_run(tmp_path / "profile", [source], [])
