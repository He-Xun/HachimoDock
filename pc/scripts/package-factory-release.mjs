/**
 * [Input] A verified ESP32-P4 factory build directory and reviewed distribution notices.
 * [Output] A source-free, no-key ZIP containing the complete factory image, binary segments, resource catalogs, and guided macOS/Windows flashing tools.
 * [Pos] Private firmware-build to public factory-flasher package boundary.
 * [Sync] If this file changes, update `scripts/.folder.md` and `firmware/BUILD.md`.
 */

import { createHash } from "node:crypto";
import {
  chmodSync,
  copyFileSync,
  cpSync,
  createReadStream,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname, join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDir, "../..");
const FACTORY_FORMAT = "pet-manager-p4-factory-v1";

function usage() {
  return `Usage:
  node scripts/package-factory-release.mjs \\
    --build-dir <esp32-p4-build-directory> \\
    --output <empty-directory> \\
    [--license <license-file>] \\
    [--notice <third-party-notices-file>]`;
}

function parseArgs(argv) {
  const result = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--help" || token === "-h") result.help = true;
    else if (["--build-dir", "--output", "--license", "--notice"].includes(token)) {
      const value = argv[index + 1];
      if (!value || value.startsWith("--")) throw new Error(`missing value for ${token}`);
      result[token.slice(2)] = value;
      index += 1;
    } else {
      throw new Error(`unexpected argument: ${token}`);
    }
  }
  return result;
}

function requireFile(path, label) {
  if (!existsSync(path) || !statSync(path).isFile() || statSync(path).size === 0) {
    throw new Error(`${label} is missing or empty: ${path}`);
  }
}

function sha256File(path) {
  return new Promise((resolveHash, reject) => {
    const digest = createHash("sha256");
    const stream = createReadStream(path);
    stream.on("data", (chunk) => digest.update(chunk));
    stream.on("error", reject);
    stream.on("end", () => resolveHash(digest.digest("hex")));
  });
}

function listFiles(root) {
  const files = [];
  function visit(directory) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      else if (entry.isFile()) files.push(path);
    }
  }
  visit(root);
  return files.sort((left, right) => relative(root, left).localeCompare(relative(root, right)));
}

function sanitizeVersion(value) {
  return value.replace(/[^0-9A-Za-z._-]+/g, "-");
}

function checkEmbeddedVersion(factoryBytes, version) {
  const descriptor = factoryBytes.subarray(0x10000 + 32, 0x10000 + 80);
  const versionField = descriptor.subarray(16);
  const nul = versionField.indexOf(0);
  if (descriptor.length !== 48 || descriptor.readUInt32LE(0) !== 0xabcd5432
      || versionField.subarray(0, nul < 0 ? versionField.length : nul).toString("latin1") !== version) {
    throw new Error("factory application's embedded board/version does not match its manifest");
  }
}

// Shown before entering the ROM loader, while the operator can still disconnect power.
const adapterBeforeFlash = "保持屏幕连接，用 USB 接入主板。工具会自动选择屏幕保护方式，并在擦写前核对设备和镜像。";
const adapterAfterFlash = "设备已自动重启。请观察第一屏和宠物动画。";

function macFlashScript(imageName, manifestName, board, sha256) {
  return `#!/bin/zsh
set -euo pipefail

script_dir="\${0:A:h}"
package_root="\${script_dir:h}"
image="\${package_root}/firmware/${imageName}"
venv="\${package_root}/.esptool-venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 Python 3.10+。请先从 https://www.python.org/downloads/ 安装。"
  exit 1
fi

if [[ ! -x "\${venv}/bin/python" ]]; then
  echo "首次运行：正在创建本地 esptool 环境……"
  python3 -m venv "\${venv}"
fi
if ! "\${venv}/bin/python" -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("esptool") and __import__("esptool").__version__ == "5.4.0" else 1)' >/dev/null 2>&1; then
  "\${venv}/bin/python" -m pip install "esptool==5.4.0"
fi

echo "${adapterBeforeFlash}"
echo "检测到的候选串口："
setopt null_glob
ports=(/dev/cu.usbserial* /dev/cu.wchusbserial* /dev/cu.usbmodem*)
for candidate in \${ports[@]}; do
  echo "  \${candidate}"
done
read "port?请输入设备串口（例如 /dev/cu.wchusbserialXXXX）："
if [[ -z "\${port}" ]]; then
  echo "未输入串口，已取消。"
  exit 1
fi

echo "本包目标板型：${board}。"
echo "警告：将擦除设备全部设置、形象和组件，并写入完整出厂镜像。"
read "confirmation?输入 ERASE 继续："
if [[ "\${confirmation}" != "ERASE" ]]; then
  echo "确认不匹配，已取消。"
  exit 1
fi

"\${venv}/bin/python" "\${script_dir}/flash_checked.py" --port "\${port}" --baud 921600 --image "\${image}" --manifest "\${package_root}/firmware/${manifestName}" --detect-board --confirm-erase "\${confirmation}" --sha256 "${sha256}"
echo "${adapterAfterFlash}"
`;
}

function windowsFlashScript(imageName, manifestName, board, sha256) {
  return `param(
    [string]$Port = ""
)

$ErrorActionPreference = "Stop"
$PackageRoot = Split-Path -Parent $PSScriptRoot
$Image = Join-Path $PackageRoot "firmware\\${imageName}"
$Venv = Join-Path $PackageRoot ".esptool-venv"
$VenvPython = Join-Path $Venv "Scripts\\python.exe"

function Invoke-Python([string[]]$Arguments) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @Arguments
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python @Arguments
    } else {
        throw "未找到 Python 3.10+。请先从 https://www.python.org/downloads/ 安装。"
    }
    if ($LASTEXITCODE -ne 0) { throw "Python 命令执行失败。" }
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "首次运行：正在创建本地 esptool 环境……"
    Invoke-Python @("-m", "venv", $Venv)
}
& $VenvPython -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("esptool") and __import__("esptool").__version__ == "5.4.0" else 1)'
if ($LASTEXITCODE -ne 0) {
    & $VenvPython -m pip install "esptool==5.4.0"
    if ($LASTEXITCODE -ne 0) { throw "esptool 安装失败。" }
}

Write-Host "${adapterBeforeFlash}"
if (-not $Port.Trim()) {
    Write-Host "检测到的串口："
    Get-CimInstance Win32_SerialPort -ErrorAction SilentlyContinue |
        Select-Object DeviceID, Name |
        Format-Table -AutoSize
    $Port = Read-Host "请输入设备串口（例如 COM5）"
}
if (-not $Port.Trim()) { throw "未输入串口，已取消。" }

Write-Host "本包目标板型：${board}。"
Write-Warning "将擦除设备全部设置、形象和组件，并写入完整出厂镜像。"
$Confirmation = Read-Host "输入 ERASE 继续"
if ($Confirmation -cne "ERASE") { throw "确认不匹配，已取消。" }

& $VenvPython (Join-Path $PSScriptRoot "flash_checked.py") --port $Port --baud 921600 --image $Image --manifest (Join-Path $PackageRoot "firmware/${manifestName}") --detect-board --confirm-erase $Confirmation --sha256 "${sha256}"
if ($LASTEXITCODE -ne 0) { throw "烧录未完成。请检查芯片版本和上方错误，勿强制忽略检查。" }
Write-Host "${adapterAfterFlash}"
`;
}

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log(usage());
  process.exit(0);
}
if (!args["build-dir"] || !args.output) throw new Error(usage());

const buildDir = resolve(args["build-dir"]);
const outputRoot = resolve(args.output);
const license = resolve(args.license || join(repositoryRoot, "LICENSE"));
const notice = resolve(args.notice || join(repositoryRoot, "THIRD_PARTY_NOTICES.md"));
const manifestPath = join(buildDir, "pet-manager-p4-factory.json");
const factoryPath = join(buildDir, "pet-manager-p4-factory.bin");
const resourceTree = join(buildDir, "factory-spiffs");
for (const [path, label] of [
  [manifestPath, "factory manifest"],
  [factoryPath, "factory image"],
  [license, "distribution license"],
  [notice, "third-party notices"],
]) requireFile(path, label);
if (!existsSync(resourceTree) || !statSync(resourceTree).isDirectory()) {
  throw new Error(`expanded factory resource tree is missing: ${resourceTree}`);
}
if (existsSync(outputRoot) && readdirSync(outputRoot).length > 0) {
  throw new Error(`output directory must be empty: ${outputRoot}`);
}

const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
if (manifest.format !== FACTORY_FORMAT || manifest.completeInstall !== true) {
  throw new Error(`manifest is not a complete ${FACTORY_FORMAT} image`);
}
if (manifest.chip !== "esp32p4" || manifest.flashOffset !== "0x0") {
  throw new Error("factory package must target ESP32-P4 from flash offset 0x0");
}
if (manifest.bytes !== statSync(factoryPath).size || manifest.sha256 !== await sha256File(factoryPath)) {
  throw new Error("factory image does not match its manifest");
}
// All three supported public layouts reserve 0x9000..0xefff for device NVS.
const factoryBytes = readFileSync(factoryPath);
if (manifest.displayProfile || !factoryBytes.subarray(0x9000, 0xf000).every(byte => byte === 0xff)) {
  throw new Error("public factory images require automatic display selection and erased device NVS");
}
// Resource locations come from the bound manifest, never from a historical
// appearance-slot address. Old factory archives remain identifiable.
const storageSegment = (manifest.segments || []).find(s => s.path === "default-terrier.spiffs.bin");
const appearanceSegment = (manifest.segments || []).find(s => s.path === "default-terrier.appearance0.bin");
if (!storageSegment || !appearanceSegment) throw new Error("factory resource segments missing");
const currentLayout = Number(storageSegment.offset) === 0x620000 && storageSegment.bytes === 0x8e0000
  && Number(appearanceSegment.offset) === 0xf00000 && appearanceSegment.bytes === 0x880000;
const legacy18Layout = Number(storageSegment.offset) === 0x520000 && storageSegment.bytes === 0x8e0000
  && Number(appearanceSegment.offset) === 0xe00000 && appearanceSegment.bytes === 0x900000;
const legacy20Layout = Number(storageSegment.offset) === 0x520000 && storageSegment.bytes === 0x6e0000
  && Number(appearanceSegment.offset) === 0xc00000 && appearanceSegment.bytes === 0xa00000;
if (!currentLayout && !legacy18Layout && !legacy20Layout) throw new Error("unrecognized factory storage/appearance layout");
if (currentLayout) {
  const expected = {nvs:[0x9000,0x6000],phy_init:[0xf000,0x1000],
    ota_0:[0x10000,0x300000],ota_1:[0x310000,0x300000],otadata:[0x610000,0x2000],
    storage:[0x620000,0x8e0000],appearance0:[0xf00000,0x880000],appearance1:[0x1780000,0x880000]};
  if (manifest.partitionLayout?.id !== "p4-storage-plus2m-ota3m-appearance17m-v2"
    || manifest.partitionLayout.appearanceBytes !== 0x1100000
    || manifest.partitionLayout.otaUpdatesPartitionTable !== false
    || Object.entries(expected).some(([name,[offset,bytes]]) => {
      const partition = manifest.partitionLayout.partitions?.[name];
      return !partition || Number(partition.offset) !== offset || partition.bytes !== bytes;
    })) throw new Error("17MiB factory manifest requires verified dual-3MiB partition layout metadata");
}
if (legacy18Layout && manifest.partitionLayout?.id !== "p4-storage-plus2m-appearance18m-v1") {
  throw new Error("legacy 18MiB factory manifest requires verified partition layout metadata");
}
const storageResourceName = `${storageSegment.offset}-${basename(storageSegment.path)}`;
const appearanceResourceName = `${appearanceSegment.offset}-${basename(appearanceSegment.path)}`;
const layoutLabel = currentLayout ? "17 MiB（8.5+8.5 MiB形象，双3 MiB OTA，storage 8.875 MiB）"
  : legacy18Layout ? "legacy 18 MiB（9+9 MiB形象，双2.5 MiB OTA，storage 8.875 MiB）"
  : "legacy 20 MiB（10+10 MiB形象，双2.5 MiB OTA）";
for (const segment of manifest.segments || []) {
  const path = join(buildDir, basename(segment.path));
  requireFile(path, `factory segment ${segment.path}`);
  if (segment.bytes !== statSync(path).size || segment.sha256 !== await sha256File(path)) {
    throw new Error(`factory segment does not match its manifest: ${segment.path}`);
  }
}

const board = manifest.boardModel;
if (!["pico", "wifi6"].includes(board) || typeof manifest.version !== "string"
  || !manifest.version.endsWith(`-${board}`)) {
  throw new Error("factory board model is missing or disagrees with firmware version");
}
const firmwareVersion = sanitizeVersion(String(manifest.version));
checkEmbeddedVersion(factoryBytes, manifest.version);
if (Number(firmwareVersion.match(/-r(\d+)(?:-|$)/)?.[1]) >= 302) {
  const font = join(resourceTree, "bubble32-v1.fpk");
  requireFile(font, "factory MiSans font");
  if (await sha256File(font) !== "c9dd60d79ed2473a447cab7e1960ff8885192df0ff8bb509492f31b6f25033e7") {
    throw new Error("factory MiSans resource failed verification");
  }
}
const chipFamily = manifest.chipFamily;
const boardModel = board === "pico" ? "ESP32-P4-Pico" : "Waveshare ESP32-P4-WIFI6";
const expectedBounds = chipFamily === "v1" ? [1, 199] : chipFamily === "v3" ? [300, 399] : null;
if (!expectedBounds || manifest.chipRevisionMin !== expectedBounds[0]
  || manifest.chipRevisionMax !== expectedBounds[1]) throw new Error("factory chip revision is missing or unsupported");
for (const offset of [0x2000, 0x10000]) {
  if (factoryBytes[offset] !== 0xe9 || factoryBytes.readUInt16LE(offset + 12) !== 18
    || factoryBytes.readUInt16LE(offset + 15) !== expectedBounds[0]
    || factoryBytes.readUInt16LE(offset + 17) !== expectedBounds[1]) {
    throw new Error("factory bootloader/application chip bounds do not match manifest");
  }
}
const packageName = `HachimoDock-P4-${chipFamily}_${firmwareVersion}_Factory-Flasher`;
const packageRoot = join(outputRoot, packageName);
const firmwareDir = join(packageRoot, "firmware");
const segmentDir = join(firmwareDir, "segments");
const resourceDir = join(packageRoot, "resources");
const toolDir = join(packageRoot, "tools");
mkdirSync(segmentDir, { recursive: true });
mkdirSync(resourceDir, { recursive: true });
mkdirSync(toolDir, { recursive: true });
for (const name of ["flash_checked.py", "p4_image_compat.py", "panel_ram_common.py", "panel_ram_v13.py", "panel_ram_v32.py"]) {
  copyFileSync(join(repositoryRoot, "firmware", "tools", name), join(toolDir, name));
}

const imageName = `HachimoDock-P4_${firmwareVersion}_factory.bin`;
const manifestName = `HachimoDock-P4_${firmwareVersion}_factory.json`;
copyFileSync(factoryPath, join(firmwareDir, imageName));
writeFileSync(
  join(firmwareDir, manifestName),
  `${JSON.stringify({ ...manifest, image: imageName }, null, 2)}\n`,
);
for (const segment of manifest.segments) {
  const source = join(buildDir, basename(segment.path));
  const offset = String(segment.offset).replace(/^0x/i, "0x");
  copyFileSync(source, join(segmentDir, `${offset}-${basename(segment.path)}`));
}
cpSync(resourceTree, join(resourceDir, "expanded-storage"), { recursive: true });
copyFileSync(license, join(packageRoot, "LICENSE"));
copyFileSync(notice, join(packageRoot, "THIRD_PARTY_NOTICES.md"));
cpSync(join(repositoryRoot, "licenses"), join(packageRoot, "licenses"), { recursive: true });

const macTool = join(toolDir, "flash-macOS.command");
const windowsTool = join(toolDir, "flash-Windows.ps1");
writeFileSync(macTool, macFlashScript(imageName, manifestName, board, manifest.sha256));
// Windows PowerShell 5.1 needs a BOM to read the Chinese safety prompts as UTF-8.
writeFileSync(windowsTool, "\uFEFF" + windowsFlashScript(imageName, manifestName, board, manifest.sha256));
chmodSync(macTool, 0o755);

writeFileSync(
  join(resourceDir, "README-CN.md"),
  `# 内置资源说明\n\n- \`expanded-storage/\`：出厂 SPIFFS 的展开内容，含默认形象索引、${manifest.components.count} 个内置组件清单及受限 PNG sprite。\n- \`../firmware/segments/${storageResourceName}\`：完整存储分区镜像。\n- \`../firmware/segments/${appearanceResourceName}\`：默认西高地 H.264 形象分区镜像。\n- 最终烧录请始终使用 \`../firmware/${imageName}\`，不要逐个写入资源文件。\n`,
);

const readme = `# HachimoDock ESP32-P4 完整出厂烧录包\n\n版本：${manifest.version}\n\n分区布局：${layoutLabel}。普通OTA不会更新分区表。新设备或明确恢复出厂可整片擦除后完整烧录；保留旧数据的布局迁移须另行准备，不能把覆盖分区表或自动格式化当成迁移。\n\n本包不只是应用固件，包含完整 factory.bin、Bootloader、分区表、OTA 元数据、默认西高地形象、${manifest.components.count} 个内置组件、校验清单，以及 Windows/macOS 引导烧录工具。\n\n## 推荐用法\n\n1. 关闭 Pet Manager。\n2. 本包适用于 ${boardModel}。WiFi6 板通过 CH343 USB-UART 烧录时，保持 USB 数据路径跳线断开。\n3. macOS 双击 \`tools/flash-macOS.command\`；Windows 右键 \`tools/flash-Windows.ps1\` 并选择“使用 PowerShell 运行”。\n4. 首次运行会在本包内创建隔离的 esptool 环境，需要 Python 3.10+ 和网络。\n5. 选择串口，确认要清空该设备后输入 \`ERASE\`。工具会自动读取设备信息；无法区分 WiFi6 与 Pico 底板时，会提示核对主板丝印。校验通过后擦除整片 32 MiB Flash，读回并确认全片为空，再把完整镜像从 \`0x0\` 写入并校验。\n\n## 重要警告\n\n该操作会覆盖设备设置、形象和组件。日常升级请使用 Pet Manager 的“固件升级”；只有新设备或明确恢复出厂时才使用本包。\n\n如果连接失败，请先核对串口并重试。配有自动下载电路的空板直接接 USB。没有自动下载电路的板子可按住 BOOT 接入 USB，进入下载模式后松开 BOOT。两种方式都保持显示排线原样。已有固件的设备请先正常启动，让工具读取屏型和休眠能力。若 921600 不稳定，可将工具脚本中的波特率改为 460800 后重新完整烧录。\n\n## 内容\n\n- \`firmware/${imageName}\`：推荐烧录的完整镜像（地址 0x0）\n- \`firmware/${manifestName}\`：镜像内容、分区和 SHA-256 清单\n- \`firmware/segments/\`：可独立核验的各二进制段，不建议普通用户分别烧录\n- \`resources/\`：默认形象及组件资源清单/分区资料\n- \`tools/\`：Windows/macOS 烧录工具\n- \`SHA256SUMS\`：包内全部文件校验值\n`;
const toolGuide = `
## HD28142C22 转接屏的完整烧录

${adapterBeforeFlash}
${adapterAfterFlash}
仅当应用通过触摸或面板 ID 确认 ST7102 时跳过 NV 休眠。仅有已保存的屏型配置时，工具仍执行原有屏幕保护流程。屏幕 ID 为全 FF 且电源状态有效时，工具沿用既有流程确认休眠后继续擦写。面板已休眠时只读复核。其他无法识别的 ID 沿用老屏回退，不需要选择屏幕。已知 NV3049F 须确认休眠；Wi-Fi 6 v1.3、v3.2 在应用确认休眠后还会进入 ROM 复核。
日常升级优先使用客户端应用 OTA，已保存的屏型会保留。

## 工具版本与 v3 接线

- 运行烧录脚本：Python **3.10+**，首次运行需要联网；脚本自动安装并核对 **esptool 5.4.0**，旧版本会自动更新。
- 本烧录包不需要 PlatformIO 或 ESP-IDF。自行编译源码才需要 PlatformIO Core **6.1.19**；v1 使用平台 55.03.32 / ESP-IDF 5.5.1，v3 使用平台 55.03.38-1 / ESP-IDF 5.5.4。
- 官方工具与说明：[esptool 5.4.0](https://github.com/espressif/esptool/releases/tag/v5.4.0)。请使用包内检查脚本，不要使用 --force 或不明确支持此芯片版本的图形烧录工具。
- **v1 与 v3 烧录包不可互刷，具体版本可以咨询客服进行确认。** ESP32-P4-WIFI6-M 的 M 不代表芯片修订版本。
- 屏幕排线连接 **DSI**，不是摄像头 **CSI**；断电后再插拔排线。配套屏幕的 v3 固件使用 24 MHz 显示时序。
- 未接摇杆时不会触发方向动作；接好摇杆、保持居中后重启，重新完成启动校准。

## 脚本无法双击时

macOS：打开终端，输入 zsh 后加空格，把 tools/flash-macOS.command 拖入终端，再回车。

Windows：在解压目录打开 PowerShell，执行：

\`\`\`powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\\tools\\flash-Windows.ps1 -Port COM5
\`\`\`

请把 COM5 改为设备实际串口。此参数只影响本次 PowerShell 进程，不修改全局执行策略。请先确认包来源和 SHA256SUMS。

烧录工具会先读取芯片版本，并核对完整镜像中的 Bootloader 和应用范围；确认匹配后才擦除、写入和校验。写入校验完成后设备会自动重启，请检查宠物画面和客户端连接。烧录速率默认 921600，必要时改为 460800；设备正常运行的串口速率为 4M，两者不同。
`;
const revisionLabel = (revision) => `${Math.floor(revision / 100)}.${revision % 100}`;
writeFileSync(join(packageRoot, "README-FLASHING-CN.md"), readme.replace(
  `版本：${manifest.version}`,
  `版本：${manifest.version}\n\n适用板型：${boardModel}。请核对开发板标识；工具核对底板和芯片修订版，Pico 与 WiFi6 不可互刷。适用芯片：ESP32-P4 ${chipFamily}（${revisionLabel(manifest.chipRevisionMin)}～${revisionLabel(manifest.chipRevisionMax)}）。Windows/macOS 共用本包；v1 与 v3 不可互刷。工具会在擦除前核对芯片版本。`,
).replaceAll("${manifest.components.count} 个内置组件", `${manifest.components.count} 个内置组件`) + toolGuide);

const contentManifest = {
  schemaVersion: 1,
  format: "hachimodock-p4-factory-flasher-v1",
  firmwareVersion: manifest.version,
  boardModel: board,
  chipFamily,
  chipRevisionMin: manifest.chipRevisionMin,
  chipRevisionMax: manifest.chipRevisionMax,
  chip: manifest.chip,
  flashOffset: manifest.flashOffset,
  factoryImage: `firmware/${imageName}`,
  appearance: manifest.appearance,
  components: manifest.components,
  publicSourceIncluded: false,
  embeddedInternalCredentials: false,
};
writeFileSync(
  join(packageRoot, "package-manifest.json"),
  `${JSON.stringify(contentManifest, null, 2)}\n`,
);

const localSecretsPath = join(repositoryRoot, "pc", ".internal-build-secrets.json");
const secretNeedles = [];
if (existsSync(localSecretsPath)) {
  const secrets = JSON.parse(readFileSync(localSecretsPath, "utf8"));
  for (const field of ["asrApiKey", "contentApiKey"]) {
    const value = typeof secrets[field] === "string" ? secrets[field].trim() : "";
    if (value) secretNeedles.push(Buffer.from(value));
  }
}
for (const path of listFiles(packageRoot)) {
  const bytes = readFileSync(path);
  if (secretNeedles.some((needle) => bytes.includes(needle))) {
    throw new Error(`factory package contains a known internal credential: ${relative(packageRoot, path)}`);
  }
}

const checksumLines = [];
for (const path of listFiles(packageRoot)) {
  const file = relative(packageRoot, path);
  checksumLines.push(`${await sha256File(path)}  ${file}`);
}
writeFileSync(join(packageRoot, "SHA256SUMS"), `${checksumLines.join("\n")}\n`);

mkdirSync(outputRoot, { recursive: true });
const archive = join(outputRoot, `${packageName}.zip`);
const zip = spawnSync("zip", ["-X", "-q", "-r", archive, packageName], {
  cwd: outputRoot,
  encoding: "utf8",
});
if (zip.status !== 0) throw new Error(zip.stderr.trim() || "zip packaging failed");

console.log(`factory flasher directory: ${packageRoot}`);
console.log(`factory flasher archive: ${archive}`);
console.log(`factory flasher SHA-256: ${await sha256File(archive)}`);
