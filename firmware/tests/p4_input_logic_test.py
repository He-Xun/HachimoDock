"""Compile and run the heap-free P4 button/rotary/joystick decoders on the host.

Input: the platform-independent decoder C source and its assertion executable.
Output: regression checks for startup calibration and four-direction joystick decoding.
Position: pytest wrapper for P4 physical-input logic.
Sync: update with pet_p4_input_core.c/.h and p4_input_logic_test.c.
"""

from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def test_invalid_startup_calibration_disarms_only_joystick():
    source = (ROOT / "main" / "pet_p4_input.c").read_text()
    assert "const bool joystick_calibrated = calibrate_joystick_center(" in source
    assert "joystick_observation_begin(joystick_calibrated,joystick_center_x,joystick_center_y);" in source
    assert "joystick_calibrated ? pet_p4_joystick_decoder_update(" in source
    assert ") : PET_P4_JOYSTICK_CENTER;" in source
    assert "PET_P4_INPUT_JOYSTICK_CENTER_DEFAULT" not in source


def test_p4_input_decoders_cover_four_direction_joystick():
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / "p4-input-test"
        subprocess.run(
            [
                "cc",
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(ROOT / "main"),
                str(ROOT / "main" / "pet_p4_input_core.c"),
                str(ROOT / "tests" / "p4_input_logic_test.c"),
                "-o",
                str(binary),
            ],
            check=True,
        )
        subprocess.run([str(binary)], check=True)


def test_home_back_stops_music_but_navigation_and_realtime_keep_priority(tmp_path):
    """Run the actual dispatch prefix with host fakes; no hardcoded physical key."""
    source = (ROOT / "main/pet_p4_input.c").read_text()
    dispatch = source[source.index("static void dispatch_binding_event("):]
    dispatch = dispatch[:dispatch.index("  const pet_p4_input_binding_t *global_exit =")]
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
typedef struct { char screen_page[24]; unsigned long long last_update_ms; } pet_p4_runtime_state_t;
typedef struct { char action[40]; } pet_p4_input_binding_t;
typedef struct { unsigned long long ts_ms; } pet_p4_input_event_t;
typedef void (*pet_p4_send_line_fn)(const char *,void *);
static pet_p4_input_binding_t configured;
static bool conversation;
static int stops, voice_exits, normal_navigation;
static const pet_p4_input_binding_t *active_binding(const char *event) {
  return !strcmp(event,"button.sw1.long_press") ? &configured : NULL;
}
static bool pet_p4_conversation_active(pet_p4_runtime_state_t *s){(void)s;return conversation;}
static void copy_text(char *out,size_t n,const char *s){snprintf(out,n,"%s",s);}
static void pet_p4_conversation_move(pet_p4_runtime_state_t *s,int d,unsigned long long t){(void)s;(void)d;(void)t;}
static void send_ignored_component_event(pet_p4_runtime_state_t *s,pet_p4_send_line_fn f,void *ctx,const pet_p4_input_event_t *e,const char *n,const char *g){(void)s;(void)f;(void)ctx;(void)e;(void)n;(void)g;}
static void pet_p4_media_stop_background(pet_p4_send_line_fn f,void *ctx){(void)f;(void)ctx;stops++;}
static void send_input_event(pet_p4_runtime_state_t *s,pet_p4_send_line_fn f,void *ctx,const pet_p4_input_event_t *e,const char *n,const char *g,const pet_p4_input_binding_t *b,const char *a,bool local){
  (void)s;(void)f;(void)ctx;(void)e;(void)n;(void)g;
  if(!strcmp(b->action,"realtime_chat")){voice_exits++;assert(!local);}
  else {assert(!strcmp(a,"media_stop"));assert(local);}
}
'''
    harness += dispatch + "  normal_navigation++;\n}\n"
    harness += r'''
int main(void) {
  pet_p4_runtime_state_t state={0};pet_p4_input_event_t event={42};
  strcpy(configured.action,"page_back");
  const char *pages[]={"app","components","main"};
  for(int i=0;i<3;i++) {
    strcpy(state.screen_page,pages[i]);
    dispatch_binding_event(&state,NULL,NULL,&event,"button.sw1.long_press","long_press");
  }
  assert(stops==1&&normal_navigation==2&&state.last_update_ms==42);
  // The old default exit key must not stop music after remapping.
  dispatch_binding_event(&state,NULL,NULL,&event,"button.sw3.short_press","short_press");
  assert(stops==1&&normal_navigation==3);
  conversation=true;
  dispatch_binding_event(&state,NULL,NULL,&event,"button.sw1.long_press","long_press");
  assert(stops==1&&voice_exits==1);
  conversation=false;strcpy(configured.action,"disabled");
  dispatch_binding_event(&state,NULL,NULL,&event,"button.sw1.long_press","long_press");
  assert(stops==1&&normal_navigation==4);
}
'''
    file = tmp_path / "home-back.c"
    file.write_text(harness)
    binary = tmp_path / "home-back"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", str(file), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

def test_pet_direction_actions_route_by_page_and_voice_scope(tmp_path):
    source = (ROOT / "main/pet_p4_input.c").read_text()
    begin = source.index("static bool apply_local_action(")
    end = source.index('  if (strcmp(binding->action, "page_toggle")', begin)
    harness = tmp_path / "navigation.c"
    harness.write_text(r'''
#include "pet_p4_protocol.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define PET_P4_SYSTEM_WIFI 1
typedef struct { const char *action; } pet_p4_input_binding_t;
static int navigation = 9, catalog = 0;
static bool conversation;
static bool pet_p4_conversation_active(const pet_p4_runtime_state_t *s) { (void)s; return conversation; }
static bool pet_p4_audio_conversation_active(void) { return false; }
static void pet_p4_lan_select_session(const pet_p4_runtime_state_t *s, int d) { (void)s; navigation = d; }
static bool pet_p4_miniapp_catalog_move(int d) { catalog = d; return true; }
static void copy_text(char *out, size_t n, const char *text) { snprintf(out, n, "%s", text); }
''' + source[begin:end] + r'''
  return false;
}
int main(void) {
  static pet_p4_runtime_state_t state;
  char result[48] = "";
  pet_p4_input_binding_t binding = {"nav_left"};
  strcpy(state.screen_page, "main");
  state.session_queue_count = 2;
  state.current_session_index = 1;
  strcpy(state.session_queue[1].id, "second");
  assert(apply_local_action(&state, &binding, 1, result, sizeof(result)));
  assert(navigation == -1 && state.current_session_index == 1);
  binding.action = "nav_right";
  assert(apply_local_action(&state, &binding, 2, result, sizeof(result)) && navigation == 1);
  binding.action = "session_next";
  assert(apply_local_action(&state, &binding, 3, result, sizeof(result)));
  assert(navigation == 0 && !strcmp(state.current_session_id, "second"));
  state.voice_feedback.phase = PET_VOICE_DRAFT;
  binding.action = "nav_left";
  assert(apply_local_action(&state, &binding, 4, result, sizeof(result)));
  assert(!strcmp(result, "navigation_blocked") && navigation == 0);
  state.voice_feedback.phase = 0;
  conversation = true;
  assert(apply_local_action(&state, &binding, 5, result, sizeof(result)) && navigation == 0);
  conversation = false;
  strcpy(state.screen_page, "components");
  assert(apply_local_action(&state, &binding, 6, result, sizeof(result)));
  assert(catalog == -1 && navigation == 0 && !strcmp(result, "component_select"));
  strcpy(state.screen_page, "app");
  assert(!apply_local_action(&state, &binding, 7, result, sizeof(result)));
}
''')
    binary = tmp_path / "navigation"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-I", str(ROOT / "main"),
                    "-I", str(ROOT / "tests/vendor/cjson"), str(harness), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
