/** [Input] Formal local library, real device inventory and existing configuration actions.
 * [Output] Community discovery, local/favorite/creator tabs, moderated publication and verified download.
 * [Sync] Update component-center/.folder.md and server API tests for behavior changes. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { useExitProtection } from "../lib/exit-protection.js";
import {
  ArrowDownToLine,
  ArrowUpRight,
  Bookmark,
  Check,
  ChevronRight,
  Heart,
  MessageCircle,
  Package,
  Pencil,
  Radio,
  Search,
  ShieldCheck,
  Sparkles,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import Button from "../shell/Button.jsx";
import DeviceScreenPreview from "./DeviceScreenPreview.jsx";
import ComponentCover from "./ComponentCover.jsx";
import {componentDisplayAuthor} from "./component-authors.js";
import {legacyAudioInstallBlockedReason} from "./library-order.js";
import {refreshGate} from "./community-refresh.js";
import {readCenterPreference,saveCenterPreference} from './catalog-state.js';
import {commentStorage,readCommentIdentity,saveCommentName} from './comment-identity.js';
import {
  COMMUNITY_TABS,
  COMMUNITY_CATEGORIES,
  FAVORITES_KEY,
  NAME_KEY,
  CONTACT_URL,
  readFavorites,
  updateFavorites,
  versionState,
  publicationStatus,
  readableBytes,
} from "./community-model.js";
import "./community.css";
import WorkflowConfirm from './WorkflowConfirm.jsx';
import CommunityWeb from "./CommunityWeb.jsx";

const request = (action, fields = {}) =>
  invoke("community_request", { input: { action, ...fields } });
const errorText = (error) =>
  typeof error === "string" ? error : error?.message || "操作未完成，请重试";
const date = (seconds) => new Date(seconds * 1000).toLocaleDateString("zh-CN");
function displayName() {
  try {
    return localStorage.getItem(NAME_KEY) || "";
  } catch {
    return "";
  }
}
function useResource(load, dependencies, identityDependencies = dependencies) {
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  const [revision, setRevision] = useState(0);
  const identity=JSON.stringify(identityDependencies);
  const previousIdentity=useRef(identity);
  useEffect(() => {
    let current = true;
    const sameRequest=previousIdentity.current===identity;
    previousIdentity.current=identity;
    setState(old=>({data:sameRequest?old.data:null,loading:true,error:""}));
    Promise.resolve()
      .then(load)
      .then(
        (data) => current && setState({ loading: false, data, error: "" }),
        (error) =>
          current &&
          setState(old=>({...old,loading:false,error:errorText(error)})),
      );
    return () => {
      current = false;
    };
    // Each caller lists the complete identity of its request. Late responses are ignored.
  }, [...dependencies, revision]);
  return { ...state, reload: () => setRevision((r) => r + 1) };
}
function Notice({ resource, children }) {
  if (resource.loading && resource.data == null)
    return (
      <div className="community-empty" role="status">
        正在加载社区…
      </div>
    );
  if (resource.error && resource.data == null)
    return (
      <div className="community-empty" role="alert">
        <strong>暂时无法连接社区</strong>
        <p>{resource.error}</p>
        <Button size="small" onClick={resource.reload}>
          重试
        </Button>
        <small>你仍然可以在“我的组件”使用本地内容。</small>
      </div>
    );
  return <>{resource.loading&&<p role="status">正在后台更新，以下暂为上次结果…</p>}{resource.error&&<p role="alert" className="community-error">更新失败，保留上次结果。{resource.error} <Button size="small" onClick={resource.reload}>重试</Button></p>}{children}</>;
}
function Dialog({ title, onClose, busy = false, children, wide = false }) {
  const ref = useRef(null),
    closeRef = useRef(onClose),
    busyRef = useRef(busy);
  closeRef.current = onClose;
  busyRef.current = busy;
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.focus();
    const key = (event) => {
      if (!ref.current?.getClientRects().length) return;
      if (event.key === "Escape" && !busyRef.current && !event.target.closest?.('[data-community-nickname-editor]')) {
        event.stopImmediatePropagation();
        closeRef.current();
      }
      if (event.key !== "Tab") return;
      const items = [
        ...(ref.current?.querySelectorAll(
          "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]",
        ) || []),
      ].filter((el) => el.getClientRects().length);
      const first = items[0],
        last = items.at(-1);
      if (!items.length) {
        event.preventDefault();
        return;
      }
      if (
        event.shiftKey &&
        (document.activeElement === first ||
          document.activeElement === ref.current)
      ) {
        event.preventDefault();
        last.focus();
      }
      if (
        !event.shiftKey &&
        (document.activeElement === last ||
          document.activeElement === ref.current)
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", key, true);
    return () => {
      document.removeEventListener("keydown", key, true);
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop community-backdrop"
      onClick={() => !busy && onClose()}
    >
      <section
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`community-dialog ${wide ? "is-wide" : ""}`}
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <h2>{title}</h2>
          <Button
            variant="icon"
            aria-label="关闭"
            disabled={busy}
            onClick={onClose}
          >
            <X size={18} />
          </Button>
        </header>
        {children}
      </section>
    </div>
  );
}

function Publish({ localItems, owned, onClose, onPublished, selectedPath }) {
  const dirty=useRef(false);
  const submitting=useRef(false);
  const [discard,setDiscard]=useState(null);
  const close=()=>{if(dirty.current)setDiscard({close:true});else onClose();};
  const [path, setPath] = useState(selectedPath || ""),
    [prepared, setPrepared] = useState(null);
  const [form, setForm] = useState({
    author: displayName(),
    summary: "",
    description: "",
    category: "tool",
    license: "",
    releaseNotes: "",
    sourceUrl: "",
  });
  const [selectedCovers, setSelectedCovers] = useState(null);
  const [readingCovers, setReadingCovers] = useState(false);
  const [busy, setBusy] = useState(false),
    [checking, setChecking] = useState(false),
    [prepareError, setPrepareError] = useState(""),
    [prepareAttempt, setPrepareAttempt] = useState(0),
    [error, setError] = useState("");
  const pending = useRef(null),
    coverSeq = useRef(0),
    prepareSeq = useRef(0);
  const publishableItems = localItems.filter(item =>
    item.isLocal && item.libraryPath && !legacyAudioInstallBlockedReason(item)
  );
  const currentPackage = prepared?.path === path ? prepared : null;
  const covers = currentPackage?.coverDataUrl ? [currentPackage.coverDataUrl, ...(selectedCovers || [])] : [];
  let publishBlocker = prepareError;
  if (!path) publishBlocker = "请先选择要发布的本地组件。";
  else if (checking) publishBlocker = "正在检查组件文件…";
  else if (!publishBlocker && !currentPackage?.canPublish)
    publishBlocker = currentPackage?.warning || "组件文件尚未通过发布检查。";
  else if (!publishBlocker && readingCovers) publishBlocker = "正在读取截图…";
  else if (!publishBlocker && !covers.length) publishBlocker = "请在组件包内添加 pc/cover.png 后重新打包。";
  useExitProtection(busy ? "社区发布尚未完成，请勿重复提交" : dirty.current ? "组件发布内容尚未提交" : "");
  useEffect(() => {
    const seq = ++prepareSeq.current;
    setPrepared(null);
    setPrepareError("");
    setError("");
    setSelectedCovers(null);
    setReadingCovers(false);
    pending.current=null;dirty.current=false;
    if (!path) {
      setChecking(false);
      return;
    }
    setChecking(true);
    request("prepare", { path })
      .then(
        (value) => {
          if (seq !== prepareSeq.current) return;
          setPrepared({ ...value, path });
          const old = owned[value.id];
          setForm((f) => ({
            ...f,
            author: old?.author || displayName(),
            sourceUrl: old?.sourceUrl || '',
            summary: old?.summary || value.description?.slice(0, 160) || "",
            description: old?.description || value.description || "",
            license: old?.license || "",
            category: old?.category || (value.kind === "game" ? "game" : "tool"),
            releaseNotes: "",
          }));
        },
        (error) => seq === prepareSeq.current && setPrepareError(errorText(error)),
      )
      .finally(() => seq === prepareSeq.current && setChecking(false));
    return () => {
      prepareSeq.current++;
    };
  }, [path, prepareAttempt]);
  async function readCovers(files) {
    const seq=prepareSeq.current;
    const selection=++coverSeq.current;
    const current=()=>seq===prepareSeq.current && selection===coverSeq.current;
    setError("");
    setReadingCovers(false);
    if (!files.length) {
      setSelectedCovers(null);
      return;
    }
    if (
      files.length > 2 ||
      files.some(
        (file) =>
          file.size > 2 * 1024 * 1024 ||
          !["image/png", "image/jpeg", "image/webp"].includes(file.type),
      )
    ) {
      setError("最多可附两张 PNG、JPEG 或 WebP 截图，每张不超过 2 MB。");
      return;
    }
    setReadingCovers(true);
    try {
      const next=await Promise.all(
          files.map(
            (file) =>
              new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result));
                reader.onerror = reject;
                reader.readAsDataURL(file);
              }),
          ),
        );
      if(current())setSelectedCovers(next);
    } catch {
      if(current())setError("截图读取失败，已保留原图。");
    } finally {
      if(current())setReadingCovers(false);
    }
  }
  async function submit(event) {
    event.preventDefault();
    if (busy || submitting.current || publishBlocker) return;
    submitting.current=true;
    setBusy(true);
    setError("");
    try {
      // Identical retries retain the same request ID, including after a restart.
      // Persist only a digest and ID, never package contents or credentials.
      const bytes = new TextEncoder().encode(
        JSON.stringify([prepared.contentHash, form, covers]),
      );
      const fingerprint = [
        ...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
      ]
        .map((x) => x.toString(16).padStart(2, "0"))
        .join("");
      const key = `pet-manager:community-publication:${prepared.id}`;
      let old;
      try {
        old = JSON.parse(localStorage.getItem(key) || "null");
      } catch {
        /* corrupt hint is not a credential */
      }
      if (pending.current?.fingerprint !== fingerprint)
        pending.current =
          old?.fingerprint === fingerprint
            ? old
            : { fingerprint, requestId: crypto.randomUUID() };
      localStorage.setItem(key, JSON.stringify(pending.current));
      const result = await request("publish", {
        path,
        metadata: form,
        covers: covers.slice(1).map((url) => url.split(",")[1]),
        expectedContentHash: prepared.contentHash,
        requestId: pending.current.requestId,
      });
      localStorage.setItem(NAME_KEY, form.author.trim());
      localStorage.removeItem(key);
      onPublished(result);
    } catch (error) {
      setError(errorText(error));
    } finally {
      submitting.current=false;
      setBusy(false);
    }
  }
  const field = (key, title, limit, multiline = false, required = true) => (
    <label>
      {title}
      {multiline ? (
        <textarea
          rows={3}
          maxLength={limit}
          value={form[key]}
          required={required}
          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
        />
      ) : (
        <input
          maxLength={limit}
          value={form[key]}
          required={required}
          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
        />
      )}
    </label>
  );
  return (
    <Dialog title="发布我的玩法" onClose={close} busy={busy||Boolean(discard)} wide>
      <form className="community-publish" onSubmit={submit} onChange={()=>{dirty.current=true;}}>
        <fieldset disabled={busy}>
        <div className="community-publish-intro">
          <Upload size={22} />
          <div>
            <h3>提交社区审核</h3>
            <p>
              选择组件后，社区会预检组件包。新作品和更新均需审核，审核通过后才会公开。
            </p>
          </div>
        </div>
        <label>
          选择组件
          <select
            aria-label="选择组件"
            value={path}
            onChange={(e) => {if(dirty.current)setDiscard({path:e.target.value});else setPath(e.target.value);}}
            disabled={busy}
            required
          >
            <option value="">{publishableItems.length ? "选择要发布的本地组件" : "暂无可发布的本地组件"}</option>
            {publishableItems.map((item) => (
                <option key={item.libraryPath} value={item.libraryPath}>
                  {item.name} · {item.version || "待检查版本"}
                </option>
              ))}
          </select>
        </label>
        {checking && <p role="status">正在检查组件文件…</p>}
        {prepareError && <div className="community-error" role="alert">
          <p>{prepareError}</p>
          <Button type="button" variant="secondary" onClick={() => setPrepareAttempt((n) => n + 1)}>重新检查</Button>
        </div>}
        {prepared && (
          <div className="community-package-check">
            <ShieldCheck size={20} />
            <div>
              <strong>
                {prepared.name} · {prepared.version}
              </strong>
              <p>
                {prepared.id} · {readableBytes(prepared.packageBytes)} ·{" "}
                {prepared.files.length} 个文件
              </p>
              {prepared.warning && <small>{prepared.warning}</small>}
              <details>
                <summary>查看将公开的文件</summary>
                <ul>
                  {prepared.files.map((name) => (
                    <li key={name}>{name}</li>
                  ))}
                </ul>
              </details>
            </div>
          </div>
        )}
        <div className="community-form-grid">
          {field("author", "公开作者名", 40)}
          <label>
            分类
            <select
              value={form.category}
              onChange={(e) => setForm({ ...form, category: e.target.value })}
            >
              {COMMUNITY_CATEGORIES.filter(([key]) => key).map(
                ([key, title]) => (
                  <option key={key} value={key}>
                    {title}
                  </option>
                ),
              )}
            </select>
          </label>
        </div>
        {field("summary", "一句话介绍", 160)}
        {field("description", "玩法与使用说明", 3000, true)}
        {field("releaseNotes", "本次更新说明（选填）", 2000, true, false)}
        {field("sourceUrl", "来源链接（选填，派生作品请注明公开来源）", 500, false, false)}
        <label>
          补充截图（选填，最多两张）
          <input
            key={path}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            disabled={busy}
            onChange={(e) => readCovers([...e.target.files])}
          />
        </label>
        {currentPackage?.coverDataUrl && (
          <small>首图来自包内封面。要更换首图，请更新组件包。</small>
        )}
        <div className="community-publish-shots">
          {covers.map((src, index) => (
            <img key={index} src={src} alt={`发布截图 ${index + 1}`} />
          ))}
        </div>
        {error && (
          <p className="community-error" role="alert">
            {error}
          </p>
        )}
        {publishBlocker && !prepareError && <p role="status">{!path && !publishableItems.length ? "还没有可发布的本地作品。请先在组件中心创建或导入自己的组件。" : publishBlocker}</p>}
        <footer>
          <small>
            需要下架或删除社区作品，请联系开发者。更新不会自动覆盖用户设备。
          </small>
          <Button
            type="submit"
            variant="primary"
            disabled={Boolean(publishBlocker)}
            loading={busy}
            loadingLabel="校验并提交…"
          >
            提交审核 <ArrowUpRight size={16} />
          </Button>
        </footer>
        </fieldset>
      </form>
      {discard&&<WorkflowConfirm title="放弃未提交修改？" description="这会清除本次填写的内容和截图。已发布的作品不受影响。" confirmLabel={discard.close?'放弃并关闭':'放弃并切换'} onCancel={()=>setDiscard(null)} onConfirm={()=>{const action=discard;setDiscard(null);dirty.current=false;if(action.close)onClose();else setPath(action.path);}}/>}
    </Dialog>
  );
}

import { navigateTabs } from "../lib/tab-navigation.js";
import { isWithdrawnComponent } from "./library-order.js";
export default function CommunityCenter({
  active = true,
  tab: controlledTab,
  capabilityTarget,
  onTabChange,
  localItems,
  inventory,
  onOpenLocal,
  onCreate,
  onImported,
  deviceInstallTargetKey,
  refreshKey,
  children,
}) {
  const [localTab,setLocalTab]=useState('local');
  const tab=controlledTab??localTab,setTab=onTabChange||setLocalTab;
  const [revision, setRevision] = useState(0),
    [detail, setDetail] = useState(""),
    [publish, setPublish] = useState(null),
    [notice, setNotice] = useState("");
  const provenance = useResource(() => request("local"), [revision], []);
  const [createdOffset, setCreatedOffset] = useState(0);
  const created = useResource(
    () => (tab === "created" ? request("mine", { offset: createdOffset }) : null),
    [tab, revision, createdOffset],
    [tab, createdOffset],
  );
  const changed = useCallback(() => setRevision((v) => v + 1), []);
  const refreshClock = useRef(null);
  if(!refreshClock.current)refreshClock.current=refreshGate();
  useEffect(()=>{
    if(!active || !["discover","favorites","created"].includes(tab))return;
    const refresh=()=>{if(document.visibilityState!=="hidden"&&refreshClock.current())changed();};
    refresh();
    const timer=setInterval(refresh,60_000);
    window.addEventListener("focus",refresh);
    document.addEventListener("visibilitychange",refresh);
    return()=>{clearInterval(timer);window.removeEventListener("focus",refresh);document.removeEventListener("visibilitychange",refresh);};
  },[active,tab,changed]);
  useEffect(() => {
    if (refreshKey) changed();
  }, [refreshKey, changed]);
  const owned = { ...provenance.data?.owned };
  for (const work of created.data?.items || []) owned[work.id] = work;
  const publicationItems=localItems.flatMap(item=>item.sourceAlternatives?.filter(source=>source.isLocal) || (item.isLocal?[item]:[]));
  const unpublished = publicationItems.filter(
    (item) =>
      item.isLocal &&
      !legacyAudioInstallBlockedReason(item) &&
      !provenance.data?.reservedIds?.includes(item.id) &&
      !owned[item.id] &&
      !provenance.data?.downloaded?.[item.id],
  );
  return (
    <div className="community-center">
      <div className="community-tabs" role="tablist" aria-label="组件中心栏目">
        {COMMUNITY_TABS.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            tabIndex={tab === id ? 0 : -1}
            onKeyDown={event => navigateTabs(event, COMMUNITY_TABS.map(([key]) => key), id, setTab)}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
        <Button variant="secondary" size="small" onClick={() => setPublish({})}>
          <Upload size={14} />
          发布作品
        </Button>
      </div>
      {notice && (
        <div className="community-notice" role="status">
          <Check size={17} />
          {notice}
          <Button
            variant="icon"
            size="small"
            onClick={() => setNotice("")}
            aria-label="关闭提示"
          >
            <X size={14} />
          </Button>
        </div>
      )}
      {["discover","favorites"].includes(tab) && <CommunityWeb capabilityTarget={capabilityTarget} key={tab} view={tab} localItems={localItems} inventory={inventory} deviceInstallTargetKey={deviceInstallTargetKey} onImported={onImported} onOpenLocal={onOpenLocal}/>}
      {tab === "local" && (
        <section role="tabpanel" aria-label="我的组件">
          <div className="community-section-title">
            <div>
              <h2>我的组件</h2>
              <p>本机内容与设备同步状态，按你的习惯配置。</p>
            </div>
            <span>{localItems.length} 个可用组件</span>
          </div>
          {children}
        </section>
      )}
      {tab === "created" && (
        <section role="tabpanel" aria-label="我的创作">
          <div className="community-section-title">
            <div>
              <h2>我的创作</h2>
              <p>在本地打磨，在社区分享。更新需使用更高版本号。</p>
            </div>
            <Button variant="primary" onClick={onCreate}>
              <Sparkles size={15} />
              创建新玩法
            </Button>
          </div>
          <Notice resource={created}>
            <div className="community-creations">
              {created.data?.items?.filter(work => !isWithdrawnComponent(work)).map((work) => {
                const local = publicationItems.find(
                  (item) => item.id === work.id && item.libraryPath,
                );
                return (
                  <article key={work.id}>
                    <div>
                      <h3>
                        {work.name}
                        <span
                          className={`community-status community-status--${work.status}`}
                        >
                          {publicationStatus(work)}
                        </span>
                      </h3>
                      <p>
                        {work.version} ·{" "}
                        {work.published && work.status !== "approved"
                          ? `公开版本仍为 ${work.published.version}`
                          : work.summary}
                      </p>
                      {work.reviewReason && (
                        <p className="community-fine">
                          审核反馈：{work.reviewReason}
                        </p>
                      )}
                    </div>
                    <Button
                      disabled={!local || work.status === "pending"}
                      onClick={() => setPublish({ path: local?.libraryPath })}
                    >
                      {work.status === "pending" ? "等待审核" : "提交更新"}
                    </Button>
                  </article>
                );
              })}
              {createdOffset === 0 && unpublished.map((item) => (
                <article key={item.id}>
                  <div>
                    <h3>
                      {item.name}
                      <span className="community-status">本地创作</span>
                    </h3>
                    <p>{item.version || "版本待检查"} · 尚未提交社区</p>
                  </div>
                  <Button
                    onClick={() => setPublish({ path: item.libraryPath })}
                  >
                    检查并发布
                  </Button>
                </article>
              ))}
            </div>
            {(createdOffset > 0 || created.data?.nextOffset != null) && <nav aria-label="作品翻页">
              <Button size="small" disabled={created.loading || createdOffset === 0}
                onClick={() => setCreatedOffset(Math.max(0, createdOffset - 30))}>上一页</Button>
              <Button size="small" disabled={created.loading || created.data?.nextOffset == null}
                onClick={() => setCreatedOffset(created.data.nextOffset)}>下一页</Button>
            </nav>}
            {createdOffset === 0 && !created.data?.items?.length && !unpublished.length && (
              <div className="community-empty">
                <Sparkles size={28} />
                <strong>从一个小想法开始</strong>
                <p>点击创建，让 Agent 使用 PetUI 生成组件；先验证，再分享。</p>
              </div>
            )}
          </Notice>
          <p className="community-fine">
            作品所有权由本机安全凭据识别，改显示名不会转移所有权。不提供权限导入导出；换机、凭据丢失或申请下架请联系开发者。
          </p>
          <Button
            variant="ghost"
            onClick={() =>
              invoke("open_external_url", { url: CONTACT_URL }).catch((e) =>
                setNotice(errorText(e)),
              )
            }
          >
            联系开发者 <ArrowUpRight size={14} />
          </Button>
          <Button variant="ghost" onClick={changed}>
            刷新审核状态
          </Button>
        </section>
      )}
      {publish && (
        <Publish
          localItems={publicationItems.filter(
            (item) =>
              !provenance.data?.reservedIds?.includes(item.id) &&
              (!provenance.data?.downloaded?.[item.id] || owned[item.id]),
          )}
          owned={owned}
          selectedPath={publish.path}
          onClose={() => setPublish(null)}
          onPublished={(work) => {
            setPublish(null);
            setCreatedOffset(0);
            setTab("created");
            setNotice(`「${work.name}」已提交审核，通过后会公开上架。`);
            changed();
          }}
        />
      )}
    </div>
  );
}
