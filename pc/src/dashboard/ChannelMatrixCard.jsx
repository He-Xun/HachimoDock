/**
 * [Input] useDeviceContext for the shared appearance, ordered follow queue and verified USB/Wi-Fi status; useToast for notices.
 * [Output] Agent matrix with USB-priority follow switching, authenticated Wi-Fi appearance sync, cancel controls, and Devin cloud configuration.
 * [Pos] component node in pc/src/dashboard
 * [Sync] If this file changes, update `pc/src/dashboard/.folder.md`.
 */

import React, { useCallback, useMemo, useState } from "react";
import {
  CheckCircle,
  Pause,
  Play,
  Repeat2,
  AudioLines,
  Braces,
  Code,
  Loader,
  Terminal,
  UploadCloud,
  X,
  Zap,
} from "lucide-react";
import {
  appearanceById,
  channelLabelForId,
} from "../lib/agent-appearance-config.js";
import { BUILTIN_TERRIER_APPEARANCE_ID } from "../lib/builtin-appearances.js";
import {
  APPEARANCE_SYNC_CANCELLED_MESSAGE,
  APPEARANCE_CHANGE_USB_REQUIRED_MESSAGE,
} from "../lib/desktop-pet-assignment.js";
import AppearancePreview from "../AppearancePreview.jsx";
import { resolveDashboardPreviewMedia } from "../lib/appearance-preview.js";
import { useDeviceContext } from "../shell/DeviceContext.jsx";
import { useDialogFocus } from "../component-center/use-dialog-focus.js";
import Card from "../shell/Card.jsx";
import { appearanceStateLabel } from "../lib/appearance-state-label.js";
import Button from "../shell/Button.jsx";
import { useToast } from "../shell/ToastStack.jsx";
import DevinConfigurationButton from "./DevinConfigurationButton.jsx";

const AGENT_ICONS = {
  "claude-code": Code,
  "cursor-agent": Terminal,
  codex: Terminal,
  openclaw: Zap,
  mimocode: Braces,
  workbuddy: Code,
  devin: Code,
  "deepseek-harness": Braces,
};

function appearanceKindLabel(appearance) {
  if (appearance?.type === "codex-import") return "Codex pet";
  if (appearance?.type === "builtin") return "内置";
  return "自定义";
}

function normalizeSyncProgress(progress = {}) {
  const bytesSent = Number(progress.bytesSent || 0);
  const bytesTotal = Number(progress.bytesTotal || 0);
  const hasBytes = Number.isSafeInteger(bytesSent) && Number.isSafeInteger(bytesTotal)
    && bytesSent > 0 && bytesSent <= bytesTotal;
  return {
    text: progress.text || "正在核对设备形象…",
    followOnly: progress.followOnly === true,
    indeterminate: !hasBytes || progress.followOnly === true || progress.indeterminate === true,
    percent: hasBytes ? progress.percent ?? Math.floor(bytesSent / bytesTotal * 100) : null,
  };
}

export default function ChannelMatrixCard({ children, subtitle, actions, onOpenPersona }) {
  const {
    appearances,
    enabledAgents,
    agentOptions,
    currentDisplay,
    usb,
    wifi,
    appearanceSync,
    refreshAppearances,
    applyDesktopPet,
    rememberAgent, enableQueuedAgent, removeQueuedAgent,
    cancelAppearanceSync,

  } = useDeviceContext();
  const { push } = useToast();

  const [pickerState, setPickerState] = useState(null); // { agentId }
  const syncing = appearanceSync?.pending === true;
  const syncProgress = appearanceSync?.progress
    ? normalizeSyncProgress(appearanceSync.progress)
    : null;

  const visibleAgents = useMemo(() => agentOptions.filter((agent) => agent?.id), [agentOptions]);
  const activeAgentId = currentDisplay.agentId;
  const canFollow = Boolean(usb?.connected || wifi);

  const closePicker = useCallback(() => setPickerState(null), []);

  const openPicker = useCallback((agentId) => {
    setPickerState({ agentId });
    refreshAppearances().catch((error) => {
      console.warn("[ChannelMatrixCard] appearance refresh failed", error);
    });
  }, [refreshAppearances]);

  const handleConfirmAppearance = useCallback(async (appearance) => {
    if (!appearance?.id) return;
    closePicker();
    try {
      const { notice } = await applyDesktopPet(appearance, {
        initialProgress: {
          text: `正在核对设备上的「${appearance.name}」…`,
          percent: 0,
          indeterminate: true,
        },
      });
      push({ tone: "success", title: notice || `已同步「${appearance.name}」到设备端` });
    } catch (err) {
      const msg = err?.message || String(err);
      const cancelled = msg === APPEARANCE_SYNC_CANCELLED_MESSAGE;
      const tone =
        cancelled || msg === APPEARANCE_CHANGE_USB_REQUIRED_MESSAGE ? "warning" : "error";
      push({
        tone,
        title: cancelled ? "形象传输已中断" : "更换形象失败",
        message: cancelled ? "已停止本次操作。" : msg,
      });
    }
  }, [applyDesktopPet, closePicker, push]);

  const changeFollow = async (operation, id) => {
    try {
      await operation(id);
    } catch (error) {
      push({ tone: "warning", title: "未能更改跟随", message: error?.message || String(error) });
    }
  };

  const handleCancelAppearanceSync = useCallback(async () => {
    try {
      await cancelAppearanceSync();
    } catch (error) {
      push({
        tone: "error",
        title: "无法中断形象传输",
        message: error?.message || String(error),
      });
    }
  }, [cancelAppearanceSync, push]);

  const appearance = currentDisplay.appearance;
  const media = resolveDashboardPreviewMedia(appearance);
  let pickerHint = "";
  if (syncing) pickerHint = "当前同步完成后可更换形象。";
  else if (!activeAgentId) pickerHint = "先在下方选择要跟随的 Agent。";
  else if (!canFollow) pickerHint = "设备连接 USB 或 Wi-Fi 后可更换形象。";
  const [playing, setPlaying] = useState(() => !window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  return (<>
    {children}
    {appearance && <section className="companion-hero" aria-label="当前跟随的桌宠">
      <div>
        <span className="kicker">{currentDisplay.appearanceUnverified ? "已配置形象 · 待设备确认" : "当前形象"}</span>
        <h2>{appearance.name}</h2>
        {activeAgentId && (enabledAgents.size <= 1 ? (
          <div className="meta">正在跟随<strong>{currentDisplay.channelLabel}</strong></div>
        ) : (
          <div className="companion-follow">
            <span className="companion-follow__label" id="companion-follow-label">正在跟随</span>
            <div className="companion-follow__options" role="group" aria-labelledby="companion-follow-label">
              {[...enabledAgents].map(id => (
                <button key={id} type="button" className="companion-follow__option"
                  aria-pressed={id === activeAgentId} onClick={() => changeFollow(rememberAgent, id)}>
                  {channelLabelForId(agentOptions, id)}
                </button>
              ))}
            </div>
          </div>
        ))}
        <div className="companion-hero__actions">
          <button type="button" className="action" onClick={() => onOpenPersona(appearance)} aria-haspopup="dialog">
            <AudioLines size={15} /> 人设和声音
          </button>
          <button type="button" className="companion-hero__switch" onClick={() => openPicker(activeAgentId)}
            disabled={Boolean(pickerHint)} aria-describedby={pickerHint ? "appearance-picker-hint" : undefined}>
            <Repeat2 size={15} /> 更换形象
          </button>
        </div>
        {pickerHint && <p id="appearance-picker-hint" className="muted small">{pickerHint}</p>}
      </div>
      <figure className="screen">
        <div className="film">
          <AppearancePreview key={appearance.id} media={media} playing={playing} className="companion-media" />
          {media.kind === "video" && <button type="button" aria-label={playing ? "暂停预览" : "播放预览"}
            onClick={() => setPlaying(value => !value)}>
            {playing ? <Pause size={14} /> : <Play size={14} />}
          </button>}
        </div>
        <figcaption><span>{media.kind === "video" ? "动作预览" : "形象预览"}</span>
          {media.kind === "video" && <span>{appearanceStateLabel(media.label)}</span>}</figcaption>
      </figure>
    </section>}
    <Card subtitle={subtitle} actions={actions}>
    <div className="channel-matrix" role="region" aria-label="选择跟随的 Agent">
      {syncProgress && (
        <div className="channel-matrix-sync" aria-live="polite">
          <div className="channel-matrix-sync__copy">
            {syncProgress.indeterminate ? <Loader size={15} className="spin" /> : <UploadCloud size={15} />}
            <div>
              <strong>{syncProgress.followOnly ? "正在切换跟随" : "正在切换形象"}</strong>
              <span>{syncProgress.text}</span>
            </div>
          </div>
          {!syncProgress.indeterminate && <div className="channel-matrix-sync__meter">
            <span>{syncProgress.percent}%</span>
            <div className="channel-matrix-sync__bar" role="progressbar"
              aria-label="形象素材传输进度" aria-valuemin={0} aria-valuemax={100}
              aria-valuenow={syncProgress.percent}>
              <span style={{ "--sync-progress": `${syncProgress.percent}%` }} />
            </div>
          </div>}
          {!syncProgress.followOnly && <Button
            className="channel-matrix-sync__cancel"
            variant="danger"
            size="small"
            loading={appearanceSync?.cancelling === true}
            loadingLabel="正在中断…"
            onClick={handleCancelAppearanceSync}
            aria-label={syncProgress.indeterminate ? "取消形象切换" : "中断形象传输"}
          >
            <X size={14} />
            {syncProgress.indeterminate ? "取消切换" : "中断传输"}
          </Button>}
        </div>
      )}

      {visibleAgents.length === 0 ? (
        <div className="channel-matrix__empty">暂时无法读取本机 Agent 列表。</div>
      ) : (
        <div className="channel-matrix__rows">
          {visibleAgents.map((agent) => {
            const Icon = AGENT_ICONS[agent.id] || Code;
            const isFollowed = enabledAgents.has(agent.id);
            const isDetected = agent.detected === true;
            return (
              <article
                key={agent.id}
                className={`channel-row${isFollowed ? " is-active" : ""}${isDetected ? "" : " is-undetected"}`}
                aria-current={activeAgentId === agent.id ? "true" : undefined}
              >
                <div className="channel-row__channel">
                  <span className="channel-row__icon"><Icon size={16} /></span>
                  <div className="channel-row__label">
                    <strong>{agent.label}</strong>
                    <span className="channel-row__status" title={agent.detail || undefined}>
                      {isDetected ? "已检测" : "未检测到客户端或 CLI"}
                    </span>
                  </div>
                </div>

                {agent.id === "devin" && (
                  <div className="channel-row__settings">
                    <DevinConfigurationButton disabled={!isDetected} />
                  </div>
                )}

                <div className="channel-row__follow">
                  {isFollowed ? (
                    enabledAgents.size === 1 ? <span className="channel-row__follow-current">已跟随</span> :
                    <button type="button" className="btn-secondary btn-sm" onClick={() => changeFollow(removeQueuedAgent, agent.id)}>取消跟随</button>
                  ) : !isDetected ? (
                    <span
                      className="channel-row__unavailable"
                      title="安装对应客户端或 CLI 后重新扫描"
                    >
                      不可用
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="btn-primary btn-sm channel-row__follow-button"
                      onClick={() => changeFollow(enableQueuedAgent, agent.id)}
                      disabled={syncing || enabledAgents.size >= 3}
                      title={enabledAgents.size >= 3 ? "最多跟随 3 个 Agent" : ""}
                    >
                      跟随
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      {pickerState && (
        <AgentAppearancePickerModal
          appearances={appearances}
          selectedAppearanceId={appearance?.id}
          syncing={syncing}
          onClose={closePicker}
          onConfirm={handleConfirmAppearance}
        />
      )}

    </div>
    </Card>
  </>);
}

function AgentAppearancePickerModal({
  appearances,
  selectedAppearanceId,
  syncing,
  onClose,
  onConfirm,
}) {
  const [selectedId, setSelectedId] = useState(selectedAppearanceId || BUILTIN_TERRIER_APPEARANCE_ID);
  const selected = appearanceById(appearances, selectedId);
  const canConfirm = Boolean(selected) && !syncing;

  const dialogRef = useDialogFocus(onClose, syncing);
  return (
    <div className="modal-backdrop" onClick={syncing ? undefined : onClose}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-label="更换形象" tabIndex={-1}
        className="modal-card modal-card--formosa-picker" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h3 className="modal-title">更换形象</h3>
            <div className="modal-subtitle">
              所有已跟随的 Agent 共用这个形象、人设和声音。
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="关闭" disabled={syncing}>
            <X size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="formosa-picker__grid">
            {appearances.map((row) => (
              <button
                type="button"
                key={row.id}
                data-card-surface="" className={`formosa-picker__option${selectedId === row.id ? " is-selected" : ""}`}
                onClick={() => setSelectedId(row.id)}
                disabled={syncing}
              >
                <div className="formosa-picker__stage" aria-hidden="true">
                <AppearancePreview
                  media={resolveDashboardPreviewMedia(row)}
                  className="formosa-picker__media"
                  emptyClassName="formosa-picker__media formosa-picker__media--empty"
                  playing={false}
                />
                </div>
                <div className="formosa-picker__copy">
                  <strong>{row.name}</strong>
                  <span>{appearanceKindLabel(row)}</span>
                </div>
                {selectedId === row.id && <CheckCircle className="formosa-picker__check" size={16} />}
              </button>
            ))}
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" type="button" onClick={onClose} disabled={syncing}>
            取消
          </button>
          <button
            className="btn-primary"
            type="button"
            onClick={() => selected && onConfirm(selected)}
            disabled={!canConfirm}
          >
            {syncing ? <Loader size={14} className="spin" /> : <UploadCloud size={14} />}
            确认
          </button>
        </div>
      </div>
    </div>
  );
}
