"""Host-only revision checks; no serial hardware or cloud access."""
from pathlib import Path
import hashlib
import json
from itertools import count
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from p4_image_compat import image_revision_range, factory_revision_range
from flash_checked import ApplicationIdentity, application_identity, flash
from panel_ram_common import PanelError, PanelIdentityUnknown


def image(minimum, maximum):
    data = bytearray(24)
    data[0] = 0xe9
    data[12] = 18
    data[15:17] = minimum.to_bytes(2, "little")
    data[17:19] = maximum.to_bytes(2, "little")
    return data


def factory(minimum, maximum, board="wifi6"):
    data = bytearray(0x10000 + 112)
    data[0x10020:0x10024] = bytes.fromhex("3254cdab")
    label = ("test-" + board).encode()
    data[0x10030:0x10030+len(label)] = label
    for offset in [0x2000, 0x10000]:
        data[offset:offset + 24] = image(minimum, maximum)
    return data


def manifest_for(image, board="wifi6", *, version="test-wifi6", minimum=300, maximum=399):
    data = image.read_bytes()
    meta = dict(version=version,format="pet-manager-p4-factory-v1", completeInstall=True, chip="esp32p4",
                flashOffset="0x0", image=image.name, boardModel=board, bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(), chipRevisionMin=minimum, chipRevisionMax=maximum)
    path = image.with_suffix(".json")
    path.write_text(json.dumps(meta))
    return path


def test_device_header_matrix(tmp_path):
    binary = tmp_path / "chip-test"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-I", str(ROOT / "main"), str(ROOT / "tests/p4_image_compat_test.c"),
                    "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_v3_build_is_separate_without_changing_transport_or_ui():
    platform = (ROOT / "platformio.ini").read_text()
    v3 = platform.split("[env:esp32_p4_evboard_v3]", 1)[1]
    assert "55.03.38-1" in v3
    assert "board_build.f_cpu = 400000000L" in v3
    assert "custom_p4_uart_baud" not in v3
    assert "monitor_speed" not in v3
    assert "custom_p4_uart_baud = 4000000" in platform
    overlay = (ROOT / "sdkconfig.v3.defaults").read_text()
    assert "CONFIG_ESP32P4_REV_MIN_300=y" in overlay
    assert "CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_400=y" in overlay
    assert "CONFIG_ESP32P4_SELECTS_REV_LESS_V3 is not set" in overlay
    cmake = (ROOT / "CMakeLists.txt").read_text()
    assert "dependencies.v3.lock" in cmake
    for name, version in [("dependencies.lock", "5.5.1"), ("dependencies.v3.lock", "5.5.4")]:
        assert "version: " + version in (ROOT / name).read_text()


def test_private_link_reference_never_overrides_normal_ui_or_windows():
    import configparser
    config=configparser.ConfigParser(interpolation=None)
    config.read(ROOT/'platformio.ini')
    assert config['env:esp32_p4_uart_2m_reference']['custom_p4_uart_baud']=='2000000'
    assert config['env:esp32_p4_uart_2m_reference']['extends']=='env:esp32_p4_canvas_candidate'
    assert config['env:esp32_p4_evboard']['custom_p4_uart_baud']=='4000000'
    for name in ('esp32_p4_canvas_candidate','esp32_p4_evboard_windows','esp32_p4_evboard_v3'):
        assert 'custom_p4_uart_baud' not in config['env:'+name]


def test_factory_rejects_unbounded_and_mixed_images():
    for lo, hi in [(1,199), (300,399)]:
        assert factory_revision_range(factory(lo,hi)) == (lo,hi)
    for lo, hi in [(0,65535), (1,399), (300,199), (200,299)]:
        with pytest.raises(ValueError):
            image_revision_range(image(lo,hi))
    mixed = factory(1,199)
    mixed[0x10000:0x10018] = image(300,399)
    with pytest.raises(ValueError):
        factory_revision_range(mixed)


@pytest.mark.parametrize("revision", [1,100,199,200,300,301,302,399,400])
@pytest.mark.parametrize("condition", ["legacy", "disconnected"])
def test_flash_checks_revision_before_any_erase_or_write(tmp_path, monkeypatch, revision, condition, capsys):
    events = []
    class Device:
        CHIP_NAME = "ESP32-P4"
        def __enter__(self): return self
        def __exit__(self, *_): events.append("closed")
        def get_chip_revision(self): events.append("revision"); return revision
        def change_baud(self, baud): events.append("baud")
        def hard_reset(self): events.append("reset")
    device = Device()
    monkeypatch.setitem(sys.modules, "esptool", types.SimpleNamespace(__version__="5.4.0", detect_chip=lambda *a,**k: device))
    def write(esp, data, **kwargs):
        assert esp is device and kwargs == {"compress": True}
        events.append("write")
    monkeypatch.setattr("flash_checked.configure_factory_flash", lambda d:events.append("capacity"))
    monkeypatch.setattr("flash_checked.confirm_factory_blank", lambda d:events.append("blank"))
    monkeypatch.setitem(sys.modules, "esptool.cmds", types.SimpleNamespace(
        run_stub=lambda d:d, erase_flash=lambda d:events.append("erase"), write_flash=write))
    path = tmp_path / "factory.bin"
    path.write_bytes(factory(300,399))
    manifest = manifest_for(path)
    monkeypatch.setattr("flash_checked.application_identity", lambda port: None)
    if 300 <= revision <= 399:
        flash(path, "test-port", manifest=manifest, board="wifi6", screen_condition=condition)
        assert "本次未确认面板休眠" in capsys.readouterr().out
        assert events == ["revision", "capacity", "baud", "erase", "blank", "write", "reset", "closed"]
    else:
        with pytest.raises(ValueError): flash(path, "test-port", manifest=manifest, board="wifi6", screen_condition=condition)
        assert events == ["revision", "closed"]


def test_flash_rejects_old_tool_before_opening_device(tmp_path, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("old tool must not open the device or write flash")
    monkeypatch.setitem(sys.modules, "esptool", types.SimpleNamespace(__version__="5.2.0", detect_chip=unexpected))
    monkeypatch.setitem(sys.modules, "esptool.cmds", types.SimpleNamespace(run_stub=unexpected, write_flash=unexpected))
    with pytest.raises(ValueError, match="5.4.0"):
        flash(tmp_path / "not-read.bin", "test-port", manifest=tmp_path/"unused", board="pico")


def test_packaged_tools_upgrade_existing_environments():
    source = (ROOT.parent / "pc/scripts/package-factory-release.mjs").read_text()
    assert source.count('esptool==5.4.0') == 2
    assert source.count('__import__("esptool").__version__ == "5.4.0"') == 2
    assert 'esptool==5.2.0' not in source

@pytest.mark.parametrize("failure", ["digest", "length", "physical_board", "unknown_board", "observed_board", "rom_device", "image_board", "pin"])
def test_flashing_stops_before_stub_or_erase(tmp_path, monkeypatch, failure):
    image = tmp_path / "factory.bin"
    data = factory(300,399)
    image.write_bytes(data)
    manifest = manifest_for(image)
    board = {"physical_board": "pico", "unknown_board": ""}.get(failure, "wifi6")
    if failure == "digest":
        data[-1] ^= 1
        image.write_bytes(data)
    if failure == "length":
        image.write_bytes(data[:-1])
    if failure == "image_board":
        board = "pico"
        manifest = manifest_for(image, board=board)
    class Rom:
        CHIP_NAME = "ESP32-P4"
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get_chip_revision(self): return 302
        def read_mac(self): return bytes(6)
    def detect(*args, **kwargs):
        assert failure == "rom_device"
        return Rom()
    monkeypatch.setitem(sys.modules, "esptool", types.SimpleNamespace(__version__="5.4.0", detect_chip=detect))
    def forbidden(*args, **kwargs): pytest.fail("must stop before stub/erase/write")
    monkeypatch.setitem(sys.modules, "esptool.cmds", types.SimpleNamespace(run_stub=forbidden, write_flash=forbidden))
    monkeypatch.setattr("flash_checked.application_identity", lambda port:
                        ApplicationIdentity("p4-111111111111", "pico" if failure == "observed_board" else "wifi6"))
    with pytest.raises(ValueError):
        flash(image, "test-port", manifest=manifest, board=board, screen_condition="legacy",
              pinned_sha256="00"*32 if failure=="pin" else None)


@pytest.mark.parametrize("model", ["wifi6", None, "pico"])
def test_factory_app_probe_reads_only_hardware_identity(monkeypatch, model):
    from flash_checked import application_identity
    events = []
    clock = count()
    monkeypatch.setattr("flash_checked.time", types.SimpleNamespace(monotonic=lambda: next(clock) / 100))
    class Serial:
        def __init__(self, **kwargs):
            assert kwargs["port"] is None
            self.responses = []
        def open(self):
            assert self.dtr is False and self.rts is False
        def write(self, request):
            query = json.loads(request)
            assert query["topic"] == "debug/identity"
            request_id = query["payload"]["requestId"]
            common = dict(boardDeviceId="p4-111111111111")
            self.responses = [
                {"topic":"debug/identity","payload":dict(common,requestId="stale",hardwareBoardModel="pico")},
                {"topic":"hello","payload":dict(common,capabilities={"firmwareUpdate":{"hardwareBoardModel":"wifi6","chipRevision":302}})},
                {"topic":"debug/identity","payload":dict(common,requestId=request_id,hardwareBoardModel=model,
                                                        prepareDisplayForFlash=True)},
            ]
        def read_until(self, size):
            assert size == 16384
            return json.dumps(self.responses.pop(0)).encode() if self.responses else b""
        def close(self): events.append("close")
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=Serial, SerialException=OSError))
    if model == "pico":
        with pytest.raises(RuntimeError, match="不一致"):
            application_identity("fixture")
    else:
        assert application_identity("fixture") == ApplicationIdentity("p4-111111111111", "wifi6", 4000000, 302, True)
    assert events == ["close"]


def test_generated_launchers_use_automatic_detection_and_pinned_digest():
    source = (ROOT.parent / "pc/scripts/package-factory-release.mjs").read_text()
    script = source[source.index("const adapterBeforeFlash"):source.index("function windowsFlashScript")]
    windows = source[source.index("function windowsFlashScript"):]
    script += windows[:windows.index("`;\n}") + 4]
    script += """
    const assert = require("node:assert/strict");
    const mac = macFlashScript("image.bin","manifest.json","wifi6","abcd");
    const win = windowsFlashScript("image.bin","manifest.json","wifi6","abcd");
    for (const output of [mac,win]) {
      assert.ok(output.includes("--manifest") && output.includes("manifest.json") && output.includes('--sha256 "abcd"'));
      assert.ok(output.includes("--detect-board") && output.includes("--confirm-erase") && output.includes("ERASE"));
      for (const manual of ["请输入屏幕条件", "factory-new", "--screen-condition", "输入实际板型"]) {
        assert.ok(!output.includes(manual));
      }
    }
    """
    subprocess.run(["node", "-e", script], check=True)


def test_factory_packager_validates_embedded_application_before_export():
    source = (ROOT.parent / "pc/scripts/package-factory-release.mjs").read_text()
    check = source[source.index("function checkEmbeddedVersion("):source.index("// Shown before entering")]
    assert source.index("checkEmbeddedVersion(factoryBytes, manifest.version)") < source.index("mkdirSync(segmentDir")
    subprocess.run(["node", "-e", check + """
        const assert = require("node:assert/strict");
        const bytes = Buffer.alloc(0x10070);
        bytes.writeUInt32LE(0xabcd5432, 0x10020);
        bytes.write("test-wifi6", 0x10030);
        checkEmbeddedVersion(bytes, "test-wifi6");
        assert.throws(() => checkEmbeddedVersion(bytes, "test-pico"));
        bytes[0x10020] = 0;
        assert.throws(() => checkEmbeddedVersion(bytes, "test-wifi6"));
        assert.throws(() => checkEmbeddedVersion(Buffer.alloc(20), "test-wifi6"));
    """], check=True)


@pytest.mark.parametrize("success", [True, False, "true", None])
def test_flash_display_requires_correlated_ack(monkeypatch, success):
    from flash_checked import prepare_display_for_flash
    clock = count()
    monkeypatch.setattr("flash_checked.time", types.SimpleNamespace(monotonic=lambda: next(clock) / 10))
    class Stream:
        def __init__(self, **params):
            assert params["port"] is None and params["baudrate"] == 4000000
        def open(self):
            assert self.rts is False and self.dtr is False
        def write(self, data):
            event = json.loads(data)
            assert event["topic"] == "system/prepare-display-flash"
            assert event["payload"]["boardDeviceId"] == "p4-111111111111"
            ack = dict(action="prepare-display-flash", requestId=event["payload"]["requestId"], ok=success)
            self.events = [dict(ack, requestId="stale", ok=True), dict(ack, action="other", ok=True)]
            if success is not None:
                self.events.append(ack)
        def read_until(self, size):
            if not self.events:
                return b""
            return json.dumps({"topic":"diagnostics/action","payload":self.events.pop(0)}).encode()
        def close(self): pass
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=Stream))
    observed = ApplicationIdentity("p4-111111111111","wifi6",4000000)
    if success is True:
        prepare_display_for_flash("port", observed)
    else:
        with pytest.raises(RuntimeError, match="休眠"):
            prepare_display_for_flash("port", observed)


@pytest.mark.parametrize("condition", ["auto", "disconnected", "legacy"])
@pytest.mark.parametrize("confirmed", [True, False])
def test_sleep_confirmation_precedes_rom_entry(tmp_path, monkeypatch, condition, confirmed):
    image = tmp_path / "factory.bin"
    image.write_bytes(factory(300, 399))
    manifest = manifest_for(image)
    observed = ApplicationIdentity("p4-000000000000", "wifi6", 4000000)
    monkeypatch.setattr("flash_checked.application_identity", lambda port: observed)
    events = []

    def prepare(port, device):
        assert port == "fixture" and device == observed
        events.append("prepare")
        if not confirmed:
            raise RuntimeError("sleep unconfirmed")

    class ReachedRom(Exception):
        pass

    def detect(*args, **kwargs):
        events.append("ROM")
        raise ReachedRom()

    def unexpected(*args, **kwargs):
        pytest.fail("unexpected stub or write")

    monkeypatch.setattr("flash_checked.prepare_display_for_flash", prepare)
    monkeypatch.setitem(sys.modules, "esptool", types.SimpleNamespace(__version__="5.4.0", detect_chip=detect))
    monkeypatch.setitem(sys.modules, "esptool.cmds", types.SimpleNamespace(run_stub=unexpected, write_flash=unexpected))
    with pytest.raises(ReachedRom if confirmed else RuntimeError):
        flash(image, "fixture", manifest=manifest, board="wifi6", screen_condition=condition)
    assert events == (["prepare", "ROM"] if confirmed else ["prepare"])


@pytest.mark.parametrize(("condition", "response"), [
    *(("auto", response) for response in ("blank", "rom", "old", "hello", "timeout", "stale")),
    *(("factory-blank", response) for response in ("old", "supported", "hello", "legacy_hello", "nack")),
])
def test_display_preflight_stops_before_reset(tmp_path, monkeypatch, condition, response):
    image_path = tmp_path / "factory.bin"
    image_path.write_bytes(factory(300, 399))
    manifest = manifest_for(image_path)
    clock = count()
    monkeypatch.setattr("flash_checked.time", types.SimpleNamespace(monotonic=lambda: next(clock) / 10))

    class Probe:
        def __init__(self, **params):
            assert params["port"] is None
        def open(self):
            assert self.rts is False and self.dtr is False
        def write(self, data):
            query = json.loads(data)
            assert query["topic"] == "debug/identity"
            identity = dict(boardDeviceId="p4-000000000000", hardwareBoardModel="wifi6",
                            requestId=query["payload"]["requestId"])
            if response in ("old", "supported"):
                if response == "supported":
                    identity["prepareDisplayForFlash"] = True
                event = {"topic": "debug/identity", "payload": identity}
            elif response == "hello":
                identity.update(prepareDisplayForFlash=True, capabilities={"firmwareUpdate": {"hardwareBoardModel": "wifi6"}})
                event = {"topic": "hello", "payload": identity}
            elif response == "legacy_hello":
                event = {"topic": "hello", "payload": {"boardDeviceId": identity["boardDeviceId"]}}
            elif response == "nack":
                event = {"topic": "protocol/ack", "payload": {
                    "requestId": identity["requestId"], "requestTopic": "debug/identity",
                    "ok": False, "code": "identity_unavailable",
                }}
            elif response == "stale":
                identity.update(requestId="previous-request", prepareDisplayForFlash=True)
                event = {"topic": "debug/identity", "payload": identity}
            else:
                self.reply = b"waiting for download\r\n" if response == "rom" else b""
                return
            self.reply = json.dumps(event).encode()
        def read_until(self, size):
            if response == "timeout":
                raise OSError("probe timeout")
            reply, self.reply = self.reply, b""
            return reply
        def close(self): pass

    def forbidden(*args, **kwargs):
        pytest.fail("unconfirmed display reached ROM or flash")
    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=Probe, SerialException=OSError))
    monkeypatch.setitem(sys.modules, "esptool", types.SimpleNamespace(__version__="5.4.0", detect_chip=forbidden))
    monkeypatch.setitem(sys.modules, "esptool.cmds", types.SimpleNamespace(run_stub=forbidden, write_flash=forbidden))
    if condition == "factory-blank":
        with pytest.raises(RuntimeError, match="检测到运行中的应用，已拒绝空板首刷"):
            flash(image_path, "fixture", manifest=manifest, board="wifi6", screen_condition=condition)
    else:
        with pytest.raises(RuntimeError, match="未能确认屏幕可休眠"):
            flash(image_path, "fixture", manifest=manifest, board="wifi6")


@pytest.mark.parametrize("condition", ["", "unknown"])
def test_cli_rejects_invalid_screen_choice_before_flash(condition):
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/flash_checked.py"), "--screen-condition=" + condition,
         "--image", "unused.bin", "--manifest", "unused.json", "--port", "unused", "--board", "wifi6"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2 and "--screen-condition: invalid choice" in result.stderr


@pytest.fixture(scope="module")
def erased_flash():
    return b"\xff" * (32 * 1024 * 1024)


@pytest.fixture
def blank_board(tmp_path, monkeypatch, erased_flash, request):
    from contextlib import nullcontext
    from unittest.mock import Mock

    board, revision = getattr(request, "param", ("wifi6", 302))
    minimum, maximum = (1, 199) if revision < 200 else (300, 399)
    image_path = tmp_path / "factory.bin"
    image_path.write_bytes(factory(minimum, maximum, board))
    manifest = manifest_for(image_path, board, version="test-" + board, minimum=minimum, maximum=maximum)
    mac = (0x24, 0x58, 0x7c, 0, 0, 1)
    calls = Mock()
    rom = calls.rom
    rom.CHIP_NAME = "ESP32-P4"
    rom.IS_STUB = False
    rom.get_chip_revision.return_value = revision
    rom.read_mac.return_value = mac
    stub = calls.stub
    stub.IS_STUB = True
    stub.flash_md5sum.side_effect = lambda address, size: hashlib.md5(
        memoryview(erased_flash)[address:address + size]
    ).hexdigest()
    calls.detect_chip.return_value = nullcontext(rom)
    calls.run_stub.return_value = stub
    calls.detect_flash_size.return_value = "32MB"
    monkeypatch.setitem(sys.modules, "esptool", types.SimpleNamespace(
        __version__="5.4.0", detect_chip=calls.detect_chip))
    monkeypatch.setitem(sys.modules, "esptool.cmds", types.SimpleNamespace(
        run_stub=calls.run_stub, erase_flash=calls.erase_flash, write_flash=calls.write_flash, attach_flash=calls.attach_flash,
        detect_flash_size=calls.detect_flash_size))

    calls.app_probe.return_value = None
    monkeypatch.setattr("flash_checked.application_identity", calls.app_probe)
    monkeypatch.setattr("flash_checked.prepare_display_for_flash", calls.prepare_display)

    for module, protect in [("panel_ram_v13", calls.protect_v13), ("panel_ram_v32", calls.protect_v32)]:
        protect.return_value = {"confirmed": True}
        monkeypatch.setitem(sys.modules, module, types.SimpleNamespace(protect=protect))

    def run(screen_condition="factory-blank", **kwargs):
        flash(image_path, "test-port", manifest=manifest, screen_condition=screen_condition,
              board=kwargs.pop("board", None if kwargs.get("detect_board") else board), **kwargs)
    return calls, run


def test_cold_factory_blank_checks_full_chip_before_write(blank_board, erased_flash, capsys):
    calls, run = blank_board
    run()
    assert [call[0] for call in calls.mock_calls] == [
        "app_probe", "detect_chip", "rom.get_chip_revision", "rom.read_mac", "protect_v32", "run_stub", "attach_flash",
        "detect_flash_size", "stub.flash_set_parameters", "stub.change_baud", "stub.flash_md5sum",
        "write_flash", "stub.hard_reset",
    ]
    calls.app_probe.assert_called_once_with("test-port", reject_running_app=True)
    calls.detect_chip.assert_called_once_with("test-port", baud=115200, connect_mode="default-reset")
    calls.protect_v32.assert_called_once_with(calls.rom)
    calls.run_stub.assert_called_once_with(calls.rom)
    calls.attach_flash.assert_called_once_with(calls.stub)
    calls.detect_flash_size.assert_called_once_with(calls.stub)
    calls.stub.flash_set_parameters.assert_called_once_with(len(erased_flash))
    calls.stub.flash_md5sum.assert_called_once_with(0, len(erased_flash))
    calls.write_flash.assert_called_once_with(calls.stub, [(0, factory(300, 399))], compress=True)
    output = capsys.readouterr().out
    assert "32 MiB Flash 已整片验空" in output
    assert "RAM 模块已确认本次面板休眠" in output


@pytest.mark.parametrize("dirty_offset", [0, 0x10000, 0x110000, (32 << 20) - 1])
@pytest.mark.parametrize("condition", ["factory-blank", "factory-new", "auto"])
def test_factory_blank_rejects_one_programmed_byte(blank_board, erased_flash, dirty_offset, condition):
    calls, run = blank_board
    chip = bytearray(erased_flash)
    chip[dirty_offset] = 0xfe
    calls.stub.flash_md5sum.side_effect = lambda addr, size: hashlib.md5(memoryview(chip)[addr:addr+size]).hexdigest()
    with pytest.raises(ValueError, match="整片 Flash"):
        run(screen_condition=condition, confirm_erase="ERASE")
    assert calls.erase_flash.call_count == (0 if condition == "factory-blank" else 1)
    calls.write_flash.assert_not_called()
    calls.stub.hard_reset.assert_not_called()


@pytest.mark.parametrize(("failure", "message"), [
    ("board", "板型"), ("pin", "SHA-256"), ("chip", "ESP32-P4"), ("revision", "芯片"),
    ("stale_stub", "运行中的 stub"), ("wrong_device", "ROM 设备"),
    ("short_mac", "MAC"), ("zero_mac", "MAC"), ("broadcast_mac", "MAC"), ("empty_id", "设备 ID"),
])
@pytest.mark.parametrize("condition", ["factory-blank", "factory-new"])
def test_first_flash_rejects_before_stub(blank_board, failure, message, condition):
    calls, run = blank_board
    kwargs = {}
    if failure == "board":
        kwargs["board"] = "pico"
    elif failure == "pin":
        kwargs["pinned_sha256"] = "0" * 64
    elif failure == "chip":
        calls.rom.CHIP_NAME = "ESP32-S3"
    elif failure == "revision":
        calls.rom.get_chip_revision.return_value = 100
    elif failure == "stale_stub":
        calls.rom.IS_STUB = True
    elif failure == "empty_id":
        kwargs["device_id"] = ""
    elif failure == "wrong_device":
        kwargs["device_id"] = "p4-24587c000002"
    else:
        calls.rom.read_mac.return_value = {"short_mac": (0x24,), "zero_mac": bytes(6),
                                          "broadcast_mac": b"\xff" * 6}[failure]
    with pytest.raises(ValueError, match=message):
        run(screen_condition=condition, confirm_erase="ERASE", **kwargs)
    calls.protect_v13.assert_not_called()
    calls.protect_v32.assert_not_called()
    if failure in ("board", "pin", "empty_id"):
        calls.detect_chip.assert_not_called()
    calls.run_stub.assert_not_called()
    calls.write_flash.assert_not_called()


@pytest.mark.parametrize("size", [None, "16MB", "64MB"])
def test_factory_blank_rejects_unknown_or_wrong_capacity(blank_board, size):
    calls, run = blank_board
    calls.detect_flash_size.return_value = size
    with pytest.raises(ValueError, match="32 MiB"):
        run()
    calls.stub.flash_md5sum.assert_not_called()
    calls.write_flash.assert_not_called()


def test_factory_blank_requires_uploaded_stub(blank_board):
    calls, run = blank_board
    calls.run_stub.return_value = calls.rom
    with pytest.raises(RuntimeError, match="RAM stub"):
        run()
    calls.attach_flash.assert_not_called()
    calls.write_flash.assert_not_called()


@pytest.mark.parametrize("reply", [None, "", "ff" * 15, bytes(16), TimeoutError("MD5 timeout")])
def test_factory_blank_digest_errors_never_write(blank_board, reply):
    calls, run = blank_board
    calls.stub.flash_md5sum.side_effect = reply if isinstance(reply, Exception) else None
    calls.stub.flash_md5sum.return_value = reply
    with pytest.raises((ValueError, TimeoutError)):
        run()
    calls.write_flash.assert_not_called()
    calls.stub.hard_reset.assert_not_called()


def test_factory_blank_automatic_download_failure_stops_before_stub(blank_board):
    calls, run = blank_board
    calls.detect_chip.side_effect = TimeoutError("ROM sync timeout")
    with pytest.raises(TimeoutError, match="ROM sync timeout"):
        run()
    calls.run_stub.assert_not_called()
    calls.write_flash.assert_not_called()


@pytest.mark.parametrize("token", [None, "", "erase", True])
def test_factory_new_requires_explicit_erase_before_probe(blank_board, token):
    calls, run = blank_board
    with pytest.raises(ValueError, match="ERASE"):
        run(screen_condition="factory-new", confirm_erase=token)
    assert not calls.mock_calls


@pytest.mark.parametrize(("blank_board", "guard"), [
    (("wifi6", 103), "protect_v13"), (("wifi6", 302), "protect_v32"),
], indirect=["blank_board"])
@pytest.mark.parametrize("old_identity", [False, True])
def test_factory_new_accepts_preloaded_after_ram_sleep(blank_board, guard, old_identity, capsys):
    calls, run = blank_board
    if old_identity:
        calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", "wifi6")
    run(screen_condition="factory-new", confirm_erase="ERASE", device_id="p4-24587c000001")
    assert [call[0] for call in calls.mock_calls] == [
        "app_probe", "detect_chip", "rom.get_chip_revision", "rom.read_mac", guard, "run_stub", "attach_flash",
        "detect_flash_size", "stub.flash_set_parameters", "stub.change_baud", "erase_flash", "stub.flash_md5sum", "write_flash", "stub.hard_reset",
    ]
    getattr(calls, guard).assert_called_once_with(calls.rom)
    assert "整片验空" in capsys.readouterr().out


@pytest.mark.parametrize("confirmed", [True, False])
def test_factory_new_prefers_app_and_stops_on_rejected_ack(blank_board, confirmed):
    calls, run = blank_board
    identity = ApplicationIdentity("p4-24587c000001", "wifi6", 4000000)
    calls.app_probe.return_value = identity
    if confirmed:
        run(screen_condition="factory-new", confirm_erase="ERASE")
        assert [call[0] for call in calls.mock_calls][:3] == ["app_probe", "prepare_display", "detect_chip"]
        calls.write_flash.assert_called_once()
    else:
        calls.prepare_display.side_effect = RuntimeError("sleep rejected")
        with pytest.raises(RuntimeError, match="sleep rejected"):
            run(screen_condition="factory-new", confirm_erase="ERASE")
        calls.detect_chip.assert_not_called()
        calls.write_flash.assert_not_called()
    calls.prepare_display.assert_called_once_with("test-port", identity)
    if confirmed:
        calls.protect_v32.assert_called_once_with(calls.rom)
    else:
        calls.protect_v32.assert_not_called()


@pytest.mark.parametrize("condition", ["factory-new", "factory-blank"])
@pytest.mark.parametrize("reply", [None, {}, {"confirmed": False}, {"confirmed": 1}, {"confirmed": "true"},
                                    TimeoutError("sleep timeout")])
def test_factory_modes_stop_on_unconfirmed_ram_sleep(blank_board, condition, reply, capsys):
    calls, run = blank_board
    if isinstance(reply, Exception):
        calls.protect_v32.side_effect = reply
    else:
        calls.protect_v32.return_value = reply
    with pytest.raises((TimeoutError, RuntimeError)):
        run(screen_condition=condition, confirm_erase="ERASE")
    calls.protect_v32.assert_called_once_with(calls.rom)
    calls.run_stub.assert_not_called()
    calls.attach_flash.assert_not_called()
    calls.write_flash.assert_not_called()
    calls.stub.hard_reset.assert_not_called()
    assert "已确认本次面板休眠" not in capsys.readouterr().out


@pytest.mark.parametrize("blank_board", [("wifi6", 301), ("wifi6", 101), ("pico", 302)], indirect=True)
def test_unknown_screen_without_ram_module_uses_legacy(blank_board, capsys):
    calls, run = blank_board
    run(screen_condition="factory-new", confirm_erase="ERASE")
    calls.protect_v13.assert_not_called()
    calls.protect_v32.assert_not_called()
    calls.write_flash.assert_called_once()
    assert "按老屏 ST7701S" in capsys.readouterr().out


def test_factory_new_stops_on_missing_module(blank_board, monkeypatch):
    calls, run = blank_board
    monkeypatch.setitem(sys.modules, "panel_ram_v32", None)
    with pytest.raises(RuntimeError, match="无法加载 panel_ram_v32"):
        run(screen_condition="factory-new", confirm_erase="ERASE")
    calls.write_flash.assert_not_called()


@pytest.mark.parametrize(("blank_board", "reported_revision", "suggest_guard"), [
    (("wifi6", 302), 302, True), (("wifi6", 103), 103, True),
    (("wifi6", 302), None, False), (("wifi6", 301), 301, False),
    (("pico", 302), 302, False),
], indirect=["blank_board"])
def test_auto_hint_requires_supported_board_revision_and_image(blank_board, reported_revision, suggest_guard):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", None, chip_revision=reported_revision)
    with pytest.raises(RuntimeError, match="保持屏幕连接") as error:
        run(screen_condition="auto")
    assert ("首刷 RAM 模块" in str(error.value)) == suggest_guard
    calls.detect_chip.assert_not_called()
    calls.write_flash.assert_not_called()


@pytest.mark.parametrize("revision", [103, 301])
def test_reported_revision_must_match_image_and_rom_before_guard(blank_board, revision):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", "wifi6", chip_revision=revision)
    with pytest.raises(ValueError, match="芯片修订"):
        run(screen_condition="factory-new", confirm_erase="ERASE")
    if revision == 103:
        calls.detect_chip.assert_not_called()
    calls.protect_v32.assert_not_called()
    calls.run_stub.assert_not_called()
    calls.write_flash.assert_not_called()


def desktop_flash(run):
    run(screen_condition="auto", detect_board=True, confirm_erase="ERASE")


@pytest.mark.parametrize("app_guard", [True, False])
def test_desktop_detects_board_once_and_selects_guard(blank_board, app_guard):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", "wifi6",
                                                       4000000 if app_guard else None, 302)
    desktop_flash(run)
    calls.app_probe.assert_called_once_with("test-port")
    if app_guard:
        calls.prepare_display.assert_called_once()
    else:
        calls.prepare_display.assert_not_called()
    calls.protect_v32.assert_called_once_with(calls.rom)
    calls.rom.read_mac.assert_called_once()
    calls.write_flash.assert_called_once()


@pytest.mark.parametrize("answer", [" WiFi6 ", "pico", "", "unknown", EOFError()])
def test_detect_board_prompts_only_for_missing_model(blank_board, monkeypatch, answer):
    from unittest.mock import Mock
    calls, run = blank_board
    prompt = Mock(side_effect=answer) if isinstance(answer, Exception) else Mock(return_value=answer)
    monkeypatch.setattr("builtins.input", prompt)
    if answer == " WiFi6 ":
        desktop_flash(run)
        calls.protect_v32.assert_called_once_with(calls.rom)
        calls.write_flash.assert_called_once()
    else:
        with pytest.raises(ValueError):
            desktop_flash(run)
        calls.detect_chip.assert_not_called()
    calls.app_probe.assert_called_once_with("test-port")
    prompt.assert_called_once()
    assert "丝印" in prompt.call_args.args[0] and "physical board" in prompt.call_args.args[0]


@pytest.mark.parametrize(("model", "revision", "board_id"), [
    ("pico", 302, "p4-24587c000001"), ("wifi6", 103, "p4-24587c000001"),
    ("wifi6", 301, "p4-24587c000001"), ("wifi6", 302, "p4-24587c000002"),
])
def test_detect_board_mismatch_blocks_guard(blank_board, model, revision, board_id):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity(board_id, model, chip_revision=revision)
    with pytest.raises(ValueError):
        desktop_flash(run)
    calls.app_probe.assert_called_once()
    calls.protect_v32.assert_not_called()
    calls.run_stub.assert_not_called()


@pytest.mark.parametrize("blank_board", [("wifi6", 302), ("pico", 302)], indirect=True)
@pytest.mark.parametrize("entry", ["desktop", "factory-blank", "factory-new"])
def test_unknown_screen_uses_legacy_without_prompt(blank_board, monkeypatch, request, entry, capsys):
    from unittest.mock import Mock
    calls, run = blank_board
    model, revision = request.node.callspec.params["blank_board"]
    if entry == "desktop":
        calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", model, chip_revision=revision)
    calls.protect_v32.side_effect = PanelIdentityUnknown("DCS ID unavailable")
    prompt = Mock(side_effect=AssertionError("unexpected prompt"))
    monkeypatch.setattr("builtins.input", prompt)
    if entry == "desktop":
        desktop_flash(run)
    else:
        run(screen_condition=entry, confirm_erase="ERASE")
    calls.write_flash.assert_called_once()
    assert "本次未确认面板休眠" in capsys.readouterr().out
    prompt.assert_not_called()


@pytest.mark.parametrize("blank_board", [("pico", 302), ("wifi6", 302)], indirect=True)
def test_nv_claim_blocks_physical_exception(blank_board, request):
    calls, run = blank_board
    model, revision = request.node.callspec.params["blank_board"]
    calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", model, chip_revision=revision, nv_reported=True)
    calls.protect_v32.side_effect = PanelIdentityUnknown("no ID")
    with pytest.raises((PanelIdentityUnknown, ValueError)):
        desktop_flash(run)
    calls.run_stub.assert_not_called()


@pytest.mark.parametrize("failure", ["app_ack", "link", "partial_nv", "sleep", "bad_confirmation"])
def test_desktop_protection_failure_never_prompts_or_writes(blank_board, failure):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", "wifi6",
                                                       4000000 if failure == "app_ack" else None)
    if failure == "app_ack":
        calls.prepare_display.side_effect = RuntimeError("ACK timeout")
    elif failure == "bad_confirmation":
        calls.protect_v32.return_value = {"confirmed": 1}
    else:
        calls.protect_v32.side_effect = PanelError(failure)
    with pytest.raises(RuntimeError):
        desktop_flash(run)
    calls.run_stub.assert_not_called()
    calls.write_flash.assert_not_called()


@pytest.mark.parametrize("board_args", [[], ["--board", "wifi6", "--detect-board"]])
def test_cli_requires_exactly_one_board_source(board_args):
    proc = subprocess.run([sys.executable, "-B", str(ROOT / "tools/flash_checked.py"),
                           "--port", "fixture", "--image", "unused", "--manifest", "unused", *board_args],
                          capture_output=True, text=True)
    assert proc.returncode == 2 and "--detect-board" in proc.stderr


def test_hello_firmware_target_is_not_a_physical_board_or_a_sleep_ack(monkeypatch):
    from flash_checked import application_identity

    clock = count()
    monkeypatch.setattr("flash_checked.time", types.SimpleNamespace(monotonic=lambda: next(clock) / 100))
    class Serial:
        def __init__(self, **kwargs):
            self.replies = []
        def open(self):
            assert self.dtr is False and self.rts is False
        def write(self, request):
            request_id = json.loads(request)["payload"]["requestId"]
            board_id = "p4-24587c000001"
            self.replies = [
                {"topic": "hello", "payload": {"boardDeviceId": board_id, "capabilities": {
                    "firmwareUpdate": {"boardModel": "wifi6", "chipRevision": 302}}}},
                {"topic": "diagnostics/report", "payload": {"runtime": {"lcdPanel": "NV3049F"}}},
                {"topic": "debug/identity", "payload": {"boardDeviceId": board_id, "requestId": request_id}},
            ]
        def read_until(self, size):
            return json.dumps(self.replies.pop(0)).encode() if self.replies else b""
        def close(self): pass

    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=Serial, SerialException=OSError))
    assert application_identity("fixture") == ApplicationIdentity("p4-24587c000001", None, None, 302, True)


def test_desktop_without_erase_never_enters_rom_without_ack(blank_board):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", "wifi6", chip_revision=302)
    with pytest.raises(RuntimeError, match="未能确认屏幕可休眠"):
        run(screen_condition="auto", detect_board=True)
    calls.detect_chip.assert_not_called()
    calls.write_flash.assert_not_called()

@pytest.mark.parametrize("condition", ["auto", "factory-new"])
@pytest.mark.parametrize("reply", [PanelIdentityUnknown("no ID after reset"), {"confirmed": False}])
def test_app_sleep_ack_cannot_bypass_post_reset_check(blank_board, condition, reply):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", "wifi6", 4000000)
    if isinstance(reply, Exception):
        calls.protect_v32.side_effect = reply
    else:
        calls.protect_v32.return_value = reply
    with pytest.raises(RuntimeError):
        run(screen_condition=condition, confirm_erase="ERASE" if condition == "factory-new" else None)
    calls.prepare_display.assert_called_once()
    calls.protect_v32.assert_called_once_with(calls.rom)
    calls.run_stub.assert_not_called()
    calls.write_flash.assert_not_called()


@pytest.mark.parametrize("topic,reason,panel,verified", [
    ("debug/identity", "verified-touch", "ST7102", True),
    ("debug/identity", "verified-panel-id", "ST7102", True),
    ("debug/identity", "configured", "ST7102", False),
    ("debug/identity", None, "ST7102", False),
    ("debug/identity", "unknown", "ST7102", False),
    ("debug/identity", "verified-touch", "ST7701S", False),
    ("stale", "verified-touch", "ST7102", False),
    ("hello", "verified-touch", "ST7102", False),
    ("diagnostics/status", "verified-panel-id", "ST7102", False),
])
def test_factory_requires_correlated_verified_st7102(blank_board, monkeypatch, topic, reason, panel, verified):
    calls, run = blank_board
    clock = count()
    monkeypatch.setattr("flash_checked.time", types.SimpleNamespace(monotonic=lambda: next(clock) / 100))

    class Serial:
        def __init__(self, **kwargs):
            self.replies = []

        def open(self):
            assert self.dtr is False and self.rts is False

        def write(self, data):
            query = json.loads(data)
            assert query["topic"] == "debug/identity"
            identity = dict(boardDeviceId="p4-24587c000001", requestId=query["payload"]["requestId"])
            payload = dict(identity, lcdPanel=panel)
            if reason is not None:
                payload["lcdSelection"] = reason
            if topic == "stale":
                self.replies.append({"topic": "debug/identity", "payload": dict(payload, requestId="old")})
            else:
                self.replies.append({"topic": topic, "payload": payload})
            if topic != "debug/identity":
                # A fresh name alone cannot borrow the reason from a different reply.
                self.replies.append({"topic": "debug/identity", "payload": dict(identity, lcdPanel=panel)})

        def read_until(self, size):
            return json.dumps(self.replies.pop(0)).encode() if self.replies else b""

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=Serial, SerialException=OSError))
    calls.app_probe.side_effect = application_identity
    run(screen_condition="factory-new", confirm_erase="ERASE")
    if verified:
        calls.protect_v32.assert_not_called()
    else:
        calls.protect_v32.assert_called_once_with(calls.rom)
    calls.write_flash.assert_called_once()


@pytest.mark.parametrize("condition,erase", [("auto", None), ("auto", "ERASE"), ("factory-new", "ERASE")])
def test_reported_st7102_skips_both_sleep_paths(blank_board, condition, erase):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity("p4-24587c000001", "wifi6", st7102_reported=True)
    run(screen_condition=condition, confirm_erase=erase, detect_board=True)
    calls.prepare_display.assert_not_called()
    calls.protect_v32.assert_not_called()
    calls.rom.read_mac.assert_called_once()
    calls.write_flash.assert_called_once()


@pytest.mark.parametrize("conflict", ["NV", "MAC"])
def test_reported_st7102_conflict_stops_before_stub(blank_board, conflict):
    calls, run = blank_board
    calls.app_probe.return_value = ApplicationIdentity(
        "p4-24587c000002" if conflict == "MAC" else "p4-24587c000001", "wifi6",
        nv_reported=conflict == "NV", st7102_reported=True)
    with pytest.raises((RuntimeError, ValueError), match="冲突|不一致"):
        desktop_flash(run)
    calls.prepare_display.assert_not_called()
    calls.protect_v32.assert_not_called()
    calls.run_stub.assert_not_called()


@pytest.mark.parametrize("condition", ["factory-blank", "factory-new"])
@pytest.mark.parametrize("initial", [0x9C, 0x98, 0x08])
def test_factory_accepts_default_id_without_app_or_panel_prompt(blank_board, condition, initial, monkeypatch):
    from unittest.mock import Mock, call
    from panel_ram_common import check_panel, DCS_WRITE

    calls, run = blank_board
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("unexpected prompt"))
    bus = Mock()
    awake = initial != 0x08
    modes = [initial] + ([0x98] if awake else []) + [8, 8]
    bus.register.side_effect = [0xFFFFFF, 255, 255, 255, *modes]
    calls.protect_v32.side_effect = lambda rom: check_panel(bus, probe_only=False)
    run(screen_condition=condition, confirm_erase="ERASE" if condition == "factory-new" else None)
    expected = [call.register(4, 3), call.register(0xDA, 1), call.register(0xDB, 1),
                call.register(0xDC, 1), call.register(0x0A)]
    if awake:
        expected.extend([call.packet(DCS_WRITE, 0x28), call.register(0x0A), call.packet(DCS_WRITE, 0x10)])
    expected.extend([call.register(0x0A), call.register(0x0A)])
    assert [c for c in bus.mock_calls if c[0] in ("packet", "register")] == expected
    calls.write_flash.assert_called_once()
