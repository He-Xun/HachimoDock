// BOE 2.83-inch ST7102 demo, esp_lcd_touch_st7123; Sitronix Protocol A v1.11.
#include "pet_p4_touch_st7123.h"
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

enum { MAX_SLOTS = 10, SLOT_BYTES = 7 };

static esp_err_t read_reg(i2c_master_dev_handle_t device, uint16_t reg, uint8_t *data, size_t len) {
  uint8_t address[] = {reg >> 8, (uint8_t)reg};
  return i2c_master_transmit_receive(device, address, sizeof(address), data, len, 50);
}

static unsigned be16(const uint8_t *p) {
  return ((unsigned)p[0] << 8) | p[1];
}

static esp_err_t identify(i2c_master_dev_handle_t device, uint8_t *slots) {
  uint8_t status;
  for (int attempt = 0; ; ++attempt) {
    esp_err_t err = read_reg(device, 0x0001, &status, 1);
    if (err != ESP_OK) return err;
    if (status == 0 || status == 4) break;  // Normal or idle scanning.
    if (status != 1 || attempt == 4) return ESP_ERR_INVALID_STATE;
    vTaskDelay(pdMS_TO_TICKS(40));
  }
  // 0005..000F: X/Y limits, slot count, scan counter, firmware revision.
  uint8_t first[11], next[11];
  esp_err_t err = read_reg(device, 0x0005, first, sizeof(first));
  if (err != ESP_OK) return err;
  unsigned x = be16(first), y = be16(first + 2);
  if (!((x == 480 && y == 640) || (x == 479 && y == 639)) ||
      first[4] == 0 || first[4] > MAX_SLOTS) return ESP_ERR_NOT_SUPPORTED;

  // Require a running scanner and stable read-only fields. There is no
  // fixed module firmware revision in the supplied example.
  for (int attempt = 0; attempt < 4; ++attempt) {
    vTaskDelay(pdMS_TO_TICKS(40));
    err = read_reg(device, 0x0005, next, sizeof(next));
    if (err != ESP_OK) return err;
    if (memcmp(first, next, 5) || memcmp(first + 7, next + 7, 4)) return ESP_ERR_INVALID_RESPONSE;
    unsigned scans = (be16(next + 5) - be16(first + 5)) & 0xffff;
    if (scans > 0 && scans < 0x8000) {
      *slots = first[4];
      return ESP_OK;
    }
  }
  return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t pet_st7123_open(i2c_master_bus_handle_t bus, pet_st7123_t *touch) {
  if (!bus || !touch) return ESP_ERR_INVALID_ARG;
  const i2c_device_config_t config = {
    .dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = PET_ST7123_ADDRESS,
    .scl_speed_hz = 100000,
  };
  i2c_master_dev_handle_t device;
  esp_err_t err = i2c_master_bus_add_device(bus, &config, &device);
  if (err != ESP_OK) return err;
  uint8_t slots;
  err = identify(device, &slots);
  if (err != ESP_OK) {
    i2c_master_bus_rm_device(device);
    return err;
  }
  *touch = (pet_st7123_t){.device = device, .slots = slots};
  return ESP_OK;
}

esp_err_t pet_st7123_read(const pet_st7123_t *touch, pet_st7123_frame_t *frame) {
  if (!touch || !touch->device || !touch->slots || touch->slots > MAX_SLOTS || !frame)
    return ESP_ERR_INVALID_ARG;
  *frame = (pet_st7123_frame_t){0};
  uint8_t flags;
  esp_err_t err = read_reg(touch->device, 0x0010, &flags, 1);
  if (err != ESP_OK) return err;
  if (flags & 0x80) return ESP_ERR_INVALID_RESPONSE;
  if (!(flags & 0x08)) return ESP_OK;
  uint8_t data[MAX_SLOTS * SLOT_BYTES];
  err = read_reg(touch->device, 0x0014, data, touch->slots * SLOT_BYTES);
  if (err != ESP_OK) return err;
  for (unsigned slot = 0; slot < touch->slots; ++slot) {
    const uint8_t *p = data + slot * SLOT_BYTES;
    if (!(p[0] & 0x80)) continue;
    unsigned x = be16(p) & 0x3fff, y = be16(p + 2) & 0x3fff;
    if (x >= 480 || y >= 640) return ESP_ERR_INVALID_RESPONSE;
    ++frame->count;
    frame->slot = slot;
    frame->x = x;
    frame->y = y;
  }
  frame->fresh = true;
  return ESP_OK;
}
