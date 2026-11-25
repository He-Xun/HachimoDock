#include <assert.h>
#include <string.h>
#include <stdatomic.h>
#include <setjmp.h>
#include <stdlib.h>
#include "pet_p4_cst328.h"
#include "pet_p4_touch_st7123.h"
#include "pet_p4_touch_core.h"

static uint8_t registers[0x5a];
static int g_address, devices, probe_result, fail_reg = -1;
static pet_st7123_t g_st7123;
static bool g_ready, scanning, revision_changes;
static unsigned waits;
static esp_err_t bsp_i2c_init(void) { return ESP_OK; }
static void *bsp_i2c_get_handle(void) { return registers; }
static int i2c_master_probe(void *bus, int addr, int timeout) {
  assert(bus == registers && addr == 0x55 && timeout > 0);
  return probe_result;
}

#define TAG "test"
#include "selection.inc"

esp_err_t i2c_master_bus_add_device(void *bus, const i2c_device_config_t *cfg, void **dev) {
  assert(bus == registers && cfg->device_address == 0x55 && cfg->scl_speed_hz == 100000);
  assert(cfg->dev_addr_length == I2C_ADDR_BIT_LEN_7 && !devices);
  *dev = registers;
  ++devices;
  return ESP_OK;
}

esp_err_t i2c_master_bus_rm_device(void *dev) {
  assert(dev == registers && devices == 1);
  --devices;
  return ESP_OK;
}

esp_err_t i2c_master_transmit_receive(void *dev, const uint8_t *a, size_t n,
                                       uint8_t *out, size_t len, int timeout) {
  assert(dev == registers && devices && n == 2 && a[0] == 0 && timeout > 0 && timeout <= 100);
  int reg = a[1];
  assert((reg == 1 && len == 1) || (reg == 5 && len == 11) ||
         (reg == 0x10 && len == 1) || (reg == 0x14 && len == registers[9] * 7u));
  if (reg == fail_reg) return ESP_ERR_TIMEOUT;
  memcpy(out, registers + reg, len);
  return ESP_OK;
}

void vTaskDelay(unsigned ms) {
  assert(ms && ++waits < 40);
  if (scanning && ++registers[11] == 0) ++registers[10];
  if (revision_changes) ++registers[15];
}

static void reject(void) {
  assert(!pet_p4_touch_try_st7123() && !g_address && !devices && !g_st7123.device);
}

// Exercise the production task for down, idle, release, down, two fingers,
// down, and an I2C fault. Stop it after the last scheduled poll.
#define BSP_LCD_PANEL_AUTO 1
enum { PET_LCD_PANEL_ST7701, PET_LCD_PANEL_ST7102 };
static int model, legacy_attempts;
static const char *g_init_stage;
static esp_err_t g_init_error;
static int pet_p4_lcd_panel_model(void) { return model; }
static esp_err_t configure_legacy_touch(void *bus) {
  assert(bus == registers);
  ++legacy_attempts;
  return ESP_ERR_NOT_FOUND;
}
#include "attach.inc"
#define PET_P4_TOUCH_SAMPLE_MS 15
#define PET_P4_CST328_SAMPLE_MS 2
#define PET_P4_TOUCH_SWIPE_THRESHOLD 48
#define PET_P4_TOUCH_LONG_PRESS_MS 700
#define ESP_LOGW(...) ((void)0)
typedef unsigned TickType_t;
typedef struct {
  bool has_pointer, has_gesture;
  pet_p4_pointer_event_t pointer;
  pet_p4_touch_event_t gesture;
} touch_sample_t;
static bool g_ft6336, g_cst826;
static void *g_touch, *g_touch_io;
static pet_cst328_info_t g_cst328_info;
static atomic_uint g_samples, g_contacts, g_poll_errors, g_consecutive_errors;
static atomic_int g_panel_x, g_panel_y, g_ui_x, g_ui_y;
typedef struct { int x, y, track_id; } esp_lcd_touch_point_data_t;
#define esp_lcd_touch_read_data(device) ((void)(device), abort(), ESP_ERR_INVALID_STATE)
#define esp_lcd_touch_get_data(...) (abort(), ESP_ERR_INVALID_STATE)
#define esp_lcd_panel_io_rx_param(device, ...) ((void)(device), abort(), ESP_ERR_INVALID_STATE)
esp_err_t pet_cst328_read(pet_cst328_sample_t *sample) {
  (void)sample;
  abort();
}
static unsigned poll;
static jmp_buf end_polls;
static uint64_t monotonic_ms(void) { return poll * 15; }
static TickType_t xTaskGetTickCount(void) { return 0; }
static void vTaskDelayUntil(TickType_t *wake, TickType_t period) {
  assert(period == 15);
  *wake += period;
  ++poll;
  if (poll == 7) longjmp(end_polls, 1);
  registers[0x10] = poll == 1 ? 0 : 8;
  registers[0x14] = poll == 4 ? 0x80 : 0;
  registers[0x14 + 9 * 7] = poll == 2 ? 0 : 0x81;
  if (poll == 6) fail_reg = 0x10;
}
static unsigned events;
static void queue_sample(touch_sample_t *s, pet_p4_touch_decoder_t *gesture,
                         pet_p4_pointer_decoder_t *pointer) {
  (void)gesture;
  (void)pointer;
  static const pet_p4_pointer_kind_t expected[] = {
    PET_P4_POINTER_DOWN, PET_P4_POINTER_UP, PET_P4_POINTER_DOWN,
    PET_P4_POINTER_CANCEL, PET_P4_POINTER_DOWN, PET_P4_POINTER_CANCEL,
  };
  assert(poll != 1 && events < sizeof(expected) / sizeof(expected[0]));
  assert(s->has_pointer && s->pointer.kind == expected[events++]);
  assert(s->pointer.x == 639 && s->pointer.y == 0);
  assert(s->has_gesture == (poll == 2));
  if (s->has_gesture) assert(s->gesture.gesture == PET_P4_TOUCH_TAP);
}
#include "polling.inc"

int main(void) {
  probe_result = ESP_ERR_TIMEOUT;
  reject();
  probe_result = ESP_OK;
  reject();  // ACK with zero registers
  const uint8_t info[] = {1,224,2,128,10,0xff,0xff,1,0,2,3};
  memcpy(registers + 5, info, sizeof(info));
  reject();  // matching geometry but no active scanner
  scanning = true;
  registers[1] = 2;
  reject();
  registers[1] = 0;
  registers[9] = 11;
  reject();
  registers[9] = 10;
  registers[6] = 225;
  reject();
  registers[6] = 224;
  fail_reg = 5;
  reject();
  fail_reg = -1;
  revision_changes = true;
  reject();
  revision_changes = false;
  g_address = 0x1a;
  assert(pet_p4_touch_try_st7123() && g_address == 0x1a && !devices && !g_st7123.device);
  g_address = 0;
  registers[10] = registers[11] = 0xff;
  probe_result = ESP_ERR_NOT_FOUND;
  reject();  // The in-cell controller is absent before the panel is awake.
  probe_result = ESP_OK;
  model = PET_LCD_PANEL_ST7701;
  assert(pet_p4_touch_init() == ESP_ERR_NOT_FOUND && !devices && legacy_attempts == 1);
  model = PET_LCD_PANEL_ST7102;
  assert(pet_p4_touch_init() == ESP_OK && g_address == 0x55 && devices == 1 && legacy_attempts == 1);
  assert(pet_p4_touch_try_st7123() && devices == 1);

  pet_st7123_frame_t frame;
  assert(pet_st7123_read(&g_st7123, &frame) == ESP_OK && !frame.fresh);
  registers[0x10] = 8;
  assert(pet_st7123_read(&g_st7123, &frame) == ESP_OK && frame.fresh && !frame.count);
  uint8_t *last = registers + 0x14 + 9 * 7;
  const int corners[][4] = {{0,0,0,479}, {479,0,0,0}, {0,639,639,479}, {479,639,639,0}};
  for (unsigned i = 0; i < 4; ++i) {
    const int *point = corners[i];
    last[0] = 0x80 | (point[0] >> 8); last[1] = point[0];
    last[2] = point[1] >> 8; last[3] = point[1];
    assert(pet_st7123_read(&g_st7123, &frame) == ESP_OK && frame.fresh);
    assert(frame.count == 1 && frame.slot == 9);
    int x, y;
    pet_p4_touch_panel_to_ui(frame.x, frame.y, &x, &y);
    assert(x == point[2] && y == point[3]);
  }
  registers[0x14] = 0x80;
  assert(pet_st7123_read(&g_st7123, &frame) == ESP_OK && frame.count == 2);
  ++last[3];
  assert(pet_st7123_read(&g_st7123, &frame) == ESP_ERR_INVALID_RESPONSE);
  fail_reg = 0x14;
  assert(pet_st7123_read(&g_st7123, &frame) == ESP_ERR_TIMEOUT && !frame.fresh);
  registers[0x10] = 0x80;
  assert(pet_st7123_read(&g_st7123, &frame) == ESP_ERR_INVALID_RESPONSE);
  fail_reg = -1;
  registers[0x10] = 8;
  registers[0x14] = 0;
  --last[3];
  if (setjmp(end_polls) == 0) touch_task(NULL);
  assert(events == 6 && g_samples == 6 && g_contacts == 3 && g_poll_errors == 1);
  assert(g_panel_x == 479 && g_panel_y == 639 && g_ui_x == 639 && g_ui_y == 0);
  return 0;
}
