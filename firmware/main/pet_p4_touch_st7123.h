#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "driver/i2c_master.h"

#define PET_ST7123_ADDRESS 0x55

typedef struct {
  i2c_master_dev_handle_t device;
  uint8_t slots;
} pet_st7123_t;

typedef struct {
  bool fresh;
  uint8_t count, slot;
  uint16_t x, y;
} pet_st7123_frame_t;

esp_err_t pet_st7123_open(i2c_master_bus_handle_t bus, pet_st7123_t *touch);
esp_err_t pet_st7123_read(const pet_st7123_t *touch, pet_st7123_frame_t *frame);
