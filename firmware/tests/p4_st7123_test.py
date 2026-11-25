"""Run the boot probe and report reader against a simulated Sitronix register map."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_st7123_module_probe_and_reports(tmp_path):
    for header in ('esp_err.h', 'driver/i2c_master.h', 'freertos/task.h', 'freertos/FreeRTOS.h'):
        path = tmp_path / header
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "cst328_host_stubs.h"\n')
    # Compile the public selection hook exactly as used at early display init.
    source = (ROOT / 'main/pet_p4_touch.c').read_text()
    start = source.index('bool pet_p4_touch_try_st7123(void) {')
    end = source.index('\n}', start) + 2
    (tmp_path / 'selection.inc').write_text(source[start:end])
    start = source.index('esp_err_t pet_p4_touch_init(void) {')
    end = source.index('  g_init_stage = "queue";', start)
    (tmp_path / 'attach.inc').write_text(source[start:end] + 'return ESP_OK;\n}\n')
    start = source.index('static void touch_task(void *arg) {')
    end = source.index('\n}', start) + 2
    (tmp_path / 'polling.inc').write_text(source[start:end])
    exe = tmp_path / 'st7123'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-I', str(tmp_path),
                    '-I', str(ROOT / 'main'), '-I', str(ROOT / 'tests'),
                    str(ROOT / 'main/pet_p4_touch_st7123.c'),
                    str(ROOT / 'main/pet_p4_touch_core.c'),
                    str(ROOT / 'tests/st7123_driver_test.c'), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
