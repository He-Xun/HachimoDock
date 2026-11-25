// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "esp_lcd_mipi_dsi.h"

esp_err_t bsp_new_st7102_panel(esp_lcd_dsi_bus_handle_t bus, esp_lcd_panel_io_handle_t io,
                               const esp_lcd_dpi_panel_config_t *config, esp_lcd_panel_handle_t *panel);
