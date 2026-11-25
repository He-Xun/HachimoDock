"""Flash a complete P4 image only after checking the connected silicon revision.

Requires esptool 5.4.0 (including P4 v3.2 stub support). Keeps one serial connection from chip detection through
erase/write/verify; no hardware is touched until the caller explicitly runs it.
"""
import argparse
from contextlib import closing
import hashlib
from importlib import import_module
import json
import re
import secrets
import time
from pathlib import Path
from typing import NamedTuple

from p4_image_compat import factory_revision_range, image_application_version


def checked_image(image, manifest_path, pinned_sha256=None):
    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    board = meta.get("boardModel")
    if board not in ("pico", "wifi6"):
        raise ValueError("工厂清单板型无效，未擦除数据")
    if (meta.get("format") != "pet-manager-p4-factory-v1" or meta.get("completeInstall") is not True
            or meta.get("chip") != "esp32p4" or meta.get("flashOffset") != "0x0"
            or meta.get("image") != image.name):
        raise ValueError("工厂清单与所选完整镜像不匹配")
    if pinned_sha256 is not None and meta.get("sha256") != pinned_sha256:
        raise ValueError("工厂清单 SHA-256 与启动器记录不匹配")
    data = image.read_bytes()
    if len(data) != meta.get("bytes") or hashlib.sha256(data).hexdigest() != meta.get("sha256"):
        raise ValueError("完整镜像长度或 SHA-256 与工厂清单不匹配")
    version = image_application_version(data[0x10000:0x10000 + 112])
    if version != meta.get("version") or not version.endswith("-" + board):
        raise ValueError("镜像中的固件版本或板型与实物、清单不匹配")
    revisions = factory_revision_range(data)
    if revisions != (meta.get("chipRevisionMin"), meta.get("chipRevisionMax")):
        raise ValueError("镜像芯片范围与清单不匹配")
    return data, revisions, board


class ApplicationIdentity(NamedTuple):
    board_id: str | None
    model: str | None
    display_sleep_baud: int | None = None
    chip_revision: int | None = None
    nv_reported: bool = False
    st7102_reported: bool = False


PANEL_RAM_MODULES = {("wifi6", 103): "panel_ram_v13", ("wifi6", 302): "panel_ram_v32"}
SCREEN_CONDITIONS = ("auto", "legacy", "disconnected", "factory-blank", "factory-new")
FACTORY_FLASH_BYTES = 32 * 1024 * 1024
# MD5 of exactly FACTORY_FLASH_BYTES erased (0xff) bytes.
ERASED_32_MIB_MD5 = "2a40671157ec2ed6375f2ec0d36fe554"


def prepare_display_for_flash(port, observed):
    """On supported NV3049F firmware require a correlated, positive sleep ACK before ROM."""
    if not observed or not observed.display_sleep_baud:
        raise RuntimeError("未能确认屏幕可休眠，已停止进入下载模式。请保持屏幕连接，核对当前运行的固件。")
    import serial
    request_id = secrets.token_hex(8)
    action = "prepare-display-flash"
    command = {"topic": "system/" + action,
               "payload": {"requestId": request_id, "boardDeviceId": observed.board_id}}
    with closing(serial.Serial(port=None, baudrate=observed.display_sleep_baud,
                               timeout=.1, write_timeout=.5)) as stream:
        stream.rts = stream.dtr = False
        stream.port = port
        stream.open()
        stream.write((json.dumps(command) + "\n").encode())
        end = time.monotonic() + 4
        while time.monotonic() < end:
            try:
                event = json.loads(stream.read_until(size=16384))
            except (ValueError, UnicodeError):
                continue
            if not isinstance(event, dict) or event.get("topic") != "diagnostics/action":
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("requestId") != request_id or payload.get("action") != action:
                continue
            if payload.get("ok") is True:
                return
            break
    raise RuntimeError("面板未确认休眠，已停止进入下载模式。请等待设备恢复。")


def reports_nv_panel(info):
    if not isinstance(info, dict):
        return False
    return (info.get("prepareDisplayForFlash") is True
            or any(str(info.get(key, "")).lower() in ("nv3049", "nv3049f", "nv3049-no-touch", "hd28142c22")
                   for key in ("lcdPanel", "lcdProfile")))


def application_identity(port, *, reject_running_app=False):
    """Bounded read-only query before ROM entry; no bind, reset, or stored credentials."""
    import serial
    observed = None
    received_identity = False
    st7102_reported = False
    nv_reported = False
    for baud in (4000000, 2000000, 3000000, 921600, 115200):
        request_id = secrets.token_hex(8)
        query = (json.dumps({"topic": "debug/identity", "payload": {"requestId": request_id}}) + "\n").encode()
        try:
            with closing(serial.Serial(port=None, baudrate=baud, timeout=.1, write_timeout=.3)) as stream:
                stream.dtr = False
                stream.rts = False
                stream.port = port
                stream.open()
                stream.write(query)
                end = time.monotonic() + .6
                while time.monotonic() < end:
                    line = stream.read_until(size=16384)
                    try:
                        event = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    # Even old firmware with no usable identity can answer the query
                    # (e.g. protocol/ack) or announce itself with hello.
                    if reject_running_app and (event.get("topic") == "hello"
                                               or payload.get("requestId") == request_id):
                        raise RuntimeError("检测到运行中的应用，已拒绝空板首刷。请使用常规完整烧录入口。")
                    caps = payload.get("capabilities")
                    update = caps.get("firmwareUpdate") if isinstance(caps, dict) else None
                    if any(reports_nv_panel(info) for info in (payload, payload.get("runtime"), update)):
                        nv_reported = True
                    board_id = payload.get("boardDeviceId", "")
                    if not isinstance(board_id, str) or not re.fullmatch(r"p4-[0-9a-f]{12}", board_id):
                        continue
                    if event.get("topic") == "debug/identity" and payload.get("requestId") == request_id:
                        info = payload
                        received_identity = True
                        st7102_reported = (
                            payload.get("lcdPanel") == "ST7102"
                            and payload.get("lcdSelection") in ("verified-touch", "verified-panel-id")
                        )
                        sleep_baud = baud if info.get("prepareDisplayForFlash") is True else None
                    elif event.get("topic") == "hello":
                        info = update if isinstance(update, dict) else {}
                        sleep_baud = None
                    else:
                        continue
                    model = info.get("hardwareBoardModel")
                    model = model if model in ("pico", "wifi6") else None
                    revision = info.get("chipRevision")
                    revision = revision if type(revision) is int else None
                    if observed:
                        if (observed.board_id != board_id
                                or (model and observed.model and model != observed.model)
                                or (revision is not None and observed.chip_revision not in (None, revision))):
                            raise RuntimeError("串口报告的设备身份、实物板型或芯片修订不一致，已停止烧录")
                        model = model or observed.model
                        revision = observed.chip_revision if revision is None else revision
                        sleep_baud = sleep_baud or observed.display_sleep_baud
                    observed = ApplicationIdentity(board_id, model, sleep_baud, revision, st7102_reported=st7102_reported)
        except (serial.SerialException, OSError, ValueError):
            pass
        if received_identity:
            break
    if nv_reported:
        return (observed or ApplicationIdentity(None, None))._replace(nv_reported=True)
    return observed


def ask_physical_board():
    try:
        answer = input("无法自动识别底板，请核对主板丝印。Check the physical board marking. "
                       "Enter pico or wifi6 (输入实际板型，回车取消): ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("pico", "wifi6"):
        raise ValueError("实物底板未确认，已取消。Physical board unconfirmed; flashing cancelled.")
    return answer


def confirm_panel_sleep(rom, board, revision, *, nv_reported):
    module_name = PANEL_RAM_MODULES.get((board, revision))
    if module_name is None:
        message = f"{board} 芯片 {revision // 100}.{revision % 100} 暂无适用的 RAM 面板休眠模块"
        if nv_reported:
            raise ValueError(message + "，已知 NV3049F 无法确认休眠，已停止烧录")
        print(message + "，按老屏 ST7701S 流程继续，本次未确认面板休眠。")
        return
    try:
        from panel_ram_common import PanelIdentityUnknown
        module = import_module(module_name)
    except ImportError as exc:
        raise RuntimeError(f"无法加载 {module_name} 面板休眠模块，已停止烧录") from exc
    try:
        result = module.protect(rom)
    except PanelIdentityUnknown as exc:
        if nv_reported:
            raise
        print(f"显示 ID 未识别（{exc}），按老屏 ST7701S 流程继续，本次未确认面板休眠。")
        return
    if isinstance(result, dict) and result.get("identification") == "st7102-id":
        if nv_reported or result.get("rdid") != "80a0fb" or result.get("individual") != "80a0fb":
            raise RuntimeError("ST7102 的 ROM 读数与面板识别证据冲突，已停止烧录")
        print("ROM 已交叉核对 ST7102 显示 ID 80 A0 FB，使用 ST7102 烧录流程。")
        return
    if not isinstance(result, dict) or result.get("confirmed") is not True:
        raise RuntimeError("RAM 模块未确认本次面板休眠，已停止烧录")
    if result.get("identification") == "default-id-power-mode":
        print("面板 ID 为 FF FF FF，型号未确定。已通过电源状态回读确认休眠。")
    elif result.get("identification") == "nv3049f-id":
        print("已通过显示接口识别 NV3049F，并确认面板休眠。")
    else:
        print("RAM 模块已确认本次面板休眠。")


def configure_factory_flash(stub):
    from esptool.cmds import attach_flash, detect_flash_size

    if not stub.IS_STUB:
        raise RuntimeError("未能启动 esptool 5.4.0 的 RAM stub，已停止首刷")
    attach_flash(stub)
    if detect_flash_size(stub) != "32MB":
        raise ValueError("未检测到 32 MiB Flash，已停止首刷")
    # Configure the RAM loader for the full address range, including the upper 16 MiB.
    stub.flash_set_parameters(FACTORY_FLASH_BYTES)


def confirm_factory_blank(stub):
    if stub.flash_md5sum(0, FACTORY_FLASH_BYTES) != ERASED_32_MIB_MD5:
        raise ValueError("整片 Flash 的 MD5 与已擦空的 32 MiB 不一致，已停止空板首刷")


def check_running_display(port, observed, board, device_id, screen_condition, revisions):
    if observed and observed.model is not None and observed.model != board:
        raise ValueError("设备报告的硬件板型与所选板型不同，未擦除数据")
    if observed and observed.board_id and device_id and observed.board_id != device_id:
        raise ValueError("运行中的设备与所选设备不同，未进入下载模式")
    revision = observed.chip_revision if observed else None
    if revision is not None and not revisions[0] <= revision <= revisions[1]:
        raise ValueError("设备报告的芯片修订与镜像不匹配，未进入下载模式")
    if observed and observed.st7102_reported:
        if observed.nv_reported or observed.display_sleep_baud:
            raise RuntimeError("ST7102 面板报告与 NV3049F 保护信息冲突，已停止烧录")
        print("应用已通过触摸或面板 ID 确认 ST7102，跳过 NV3049F 面板休眠。")
        return False
    if screen_condition == "auto" and not (observed and observed.display_sleep_baud):
        message = "未能确认屏幕可休眠，应用未报告可用的休眠能力。已停止进入下载模式，请保持屏幕连接。"
        if (board, revision) in PANEL_RAM_MODULES:
            message += (f"已核对的 {board} {revision // 100}.{revision % 100} 有首刷 RAM 模块。"
                        "如确需清空设备，请在引导烧录工具中输入 ERASE，由模块实测面板休眠后再写入。")
        else:
            message += "请核对实物板型、确切的芯片修订及当前固件。"
        raise RuntimeError(message)
    if screen_condition == "auto" or (observed and observed.display_sleep_baud):
        prepare_display_for_flash(port, observed)
        print("设备已确认本次面板休眠。")
        return True
    if observed and observed.nv_reported and screen_condition in ("legacy", "disconnected"):
        raise RuntimeError("设备报告 NV3049F，无法确认休眠，已停止烧录")
    if screen_condition == "legacy":
        print("按操作员确认的非 NV3049F 老屏继续，本次未确认面板休眠。")
    elif screen_condition == "disconnected":
        print("按操作员确认的屏幕已断开状态继续，本次未确认面板休眠。")
    return False


def flash(image: Path, port: str, baud: int = 921600, *, manifest: Path, board: str | None = None,
          detect_board=False, device_id=None, pinned_sha256=None, screen_condition="auto", confirm_erase=None):
    import esptool
    from esptool.cmds import run_stub, write_flash

    if esptool.__version__ != "5.4.0":
        raise ValueError("请安装 esptool==5.4.0 后重试；旧版烧录工具不支持此 P4 芯片，未擦除数据")
    if (detect_board and board is not None) or (not detect_board and board is None):
        raise ValueError("请在 --detect-board 和 --board 之间选择一项")
    data, (minimum, maximum), package_board = checked_image(image, manifest, pinned_sha256)
    if not detect_board and board != package_board:
        raise ValueError("实物板型与工厂清单不匹配，未擦除数据")
    if screen_condition not in SCREEN_CONDITIONS:
        raise ValueError("请选择有效的实物屏幕条件，未进入下载模式")
    if device_id is not None and not re.fullmatch(r"p4-[0-9a-f]{12}", device_id):
        raise ValueError("预期设备 ID 格式无效，未进入下载模式")
    if confirm_erase is not None and confirm_erase != "ERASE":
        raise ValueError("清除数据确认不匹配，请输入 ERASE")
    # Both entry points follow the same app-first, ROM-guarded flash path.
    automatic_flash = screen_condition == "auto" and confirm_erase == "ERASE"
    if automatic_flash:
        screen_condition = "factory-new"
    factory_blank = screen_condition == "factory-blank"
    first_flash = factory_blank or screen_condition == "factory-new"
    if screen_condition == "factory-new" and confirm_erase != "ERASE":
        raise ValueError("新板首刷将清除厂商预装应用及已有数据，请输入 ERASE 确认")
    if len(data) > FACTORY_FLASH_BYTES:
        raise ValueError("首刷镜像超出 32 MiB Flash 容量")
    if factory_blank:
        observed = application_identity(port, reject_running_app=True)
    else:
        observed = application_identity(port)
    if detect_board:
        board = observed.model if observed and observed.model else ask_physical_board()
        if board != package_board:
            raise ValueError("实物板型与工厂清单不匹配，未进入下载模式")
    app_sleep_confirmed = check_running_display(port, observed, board, device_id, screen_condition,
                                               (minimum, maximum))
    with esptool.detect_chip(port, baud=115200, connect_mode="default-reset") as connected:
        if connected.CHIP_NAME != "ESP32-P4":
            raise ValueError("设备不是 ESP32-P4，已取消烧录，未擦除数据")
        if first_flash and connected.IS_STUB:
            raise ValueError("自动下载连接到了运行中的 stub，已停止首刷")
        revision = connected.get_chip_revision()
        if not minimum <= revision <= maximum:
            raise ValueError(f"芯片 {revision // 100}.{revision % 100} 与固件不匹配，已取消烧录，未擦除数据")
        if observed and observed.chip_revision is not None and observed.chip_revision != revision:
            raise ValueError("ROM 芯片修订与应用报告不一致，已停止烧录")
        st7102 = bool(observed and observed.st7102_reported)
        if detect_board or first_flash or observed or device_id:
            mac = bytes(connected.read_mac())
            if (detect_board or first_flash or st7102) and (len(mac) != 6 or not any(mac) or mac[0] & 1):
                raise ValueError("ROM 未报告有效的设备 MAC，已停止首刷")
            rom_id = "p4-" + mac.hex()
            if (observed and observed.board_id and rom_id != observed.board_id) or (device_id and rom_id != device_id):
                raise ValueError("ROM 设备与已核对的实物设备不一致，未擦除数据")
        # The paired DSI headers use native ROM WRITE_REG on this verified connection.
        # Entering the downloader resets the P4 after the application's sleep ACK.
        # Recheck in ROM before starting the flash stub on supported hardware.
        recheck_after_reset = app_sleep_confirmed and (board, revision) in PANEL_RAM_MODULES
        if recheck_after_reset:
            print("主控已进入下载模式，正在重新读回面板休眠状态。")
        if recheck_after_reset or (first_flash and not app_sleep_confirmed and not st7102):
            confirm_panel_sleep(connected, board, revision,
                                nv_reported=app_sleep_confirmed or bool(observed and observed.nv_reported))
        device = run_stub(connected)
        configure_factory_flash(device)
        device.change_baud(baud)
        if not factory_blank:
            from esptool.cmds import erase_flash
            erase_flash(device)
        confirm_factory_blank(device)
        print("32 MiB Flash 已整片验空，开始写入完整镜像。")
        write_flash(device, [(0, data)], compress=True)
        device.hard_reset()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="验证芯片版本后烧录完整 P4 镜像（将清空设备）")
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    boards = parser.add_mutually_exclusive_group(required=True)
    boards.add_argument("--board", choices=("pico", "wifi6"), help="操作员核对的实体板型")
    boards.add_argument("--detect-board", action="store_true", help="自动识别底板，仅在未知时询问实物丝印")
    parser.add_argument("--screen-condition", choices=SCREEN_CONDITIONS, default="auto",
                        help="auto 在应用通过触摸或面板 ID 确认 ST7102 时跳过 NV 休眠，其他情况优先要求应用确认休眠，已确认 ERASE 时也可通过 ROM 确认休眠；legacy 表示已核实为非 NV3049F 老屏；"
                             "disconnected 表示已核实屏幕未连接；factory-blank 仅用于操作员已确认"
                             "屏幕保持安装、整机冷态首次上电的空板。检测到应用响应即停止，否则由工具自动进入下载模式，"
                             "须分别确认整片 32 MiB Flash 已擦空和面板已休眠。factory-new 用于新板或厂商预装板，"
                             "优先通过运行中的应用确认休眠，否则使用对应芯片的 RAM 模块确认休眠")
    parser.add_argument("--confirm-erase", choices=("ERASE",), help="传入操作员输入的 ERASE，启用 auto 的 ROM 保护路径；factory-new 必填")
    parser.add_argument("--sha256", help="打包时记录的完整镜像 SHA-256")
    parser.add_argument("--device-id", help="可选的已核对设备 ID（p4- 加 12 位小写 MAC），须与 ROM 一致")
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=921600)
    args = parser.parse_args()
    flash(args.image, args.port, args.baud, manifest=args.manifest, board=args.board, pinned_sha256=args.sha256,
          detect_board=args.detect_board, device_id=args.device_id, screen_condition=args.screen_condition, confirm_erase=args.confirm_erase)
