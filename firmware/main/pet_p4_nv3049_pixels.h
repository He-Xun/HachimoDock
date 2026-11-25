/* Native RGB565 renderer boundary for the installed NV3049F panel. */
#pragma once
#include <stdbool.h>
#include <stdint.h>

static inline bool pet_nv3049_blit_rgb565(uint8_t *dst, int x, int y, int w, int h,
                                        const uint16_t *src) {
  if (!dst || !src || x < 0 || y < 0 || w <= 0 || h <= 0 || w > 480 || h > 640 ||
      x > 480 - w || y > 640 - h) return false;

  /* R5 scanout reverses both native axes for either NV3049F assembly. */
  for (int row = 0; row < h; ++row) {
    const int first = (639 - y - row) * 480 + (479 - x);
    for (int col = 0; col < w; ++col) {
      uint8_t *out = dst + (first - col) * 3;
      uint16_t c = *src++;
      unsigned r = (c >> 11) & 31, g = (c >> 5) & 63, b = c & 31;
      out[0] = (uint8_t)((b << 3) | (b >> 2));
      out[1] = (uint8_t)((g << 2) | (g >> 4));
      out[2] = (uint8_t)((r << 3) | (r >> 2));
    }
  }
  return true;
}
