/**
 * Bridge layer — seam between UI and Python backend (via QWebChannel).
 * Falls back to mock implementation when running outside Qt shell (e.g. web dev).
 */

export interface VideoFormat {
  resolutionLabel: string;
  height: number;
  fps: number;
  vcodec: string;
  acodec: string;
  estimatedSizeMB: number;
  estimatedSizeCompatMB?: number;
}

export interface VideoInfo {
  platform?: "youtube" | "tiktok";
  mediaType?: "video" | "photo";
  title: string;
  thumbnailUrl: string;
  durationSec: number;
  channel: string;
  isPlaylist: boolean;
  isYouTubePlaylist?: boolean;
  capabilities?: {
    supportsSubtitles: boolean;
    supportsMp3: boolean;
    supportsTrim: boolean;
    supportsNativeAudio: boolean;
  };
  playlistEntries?: { id: string; title: string; thumbnailUrl: string }[];
  formats: VideoFormat[];
}

export type TaskStatus =
  | "QUEUED"
  | "FETCHING_INFO"
  | "DOWNLOADING"
  | "MERGING"
  | "DONE"
  | "ERROR"
  | "CANCELLING"
  | "CANCELLED";

export interface TaskProgress {
  taskId: string;
  status: TaskStatus;
  percent: number;
  speedKBs?: number;
  etaSec?: number;
  errorMessage?: string;
}

export interface Bridge {
  fetchVideoInfo(url: string): Promise<VideoInfo>;
  startVideoDownload(params: {
    url: string;
    height: number;
    outputFormat?: "best" | "compat";
    outputDir?: string;
    taskId?: string;
    startSec?: number;
    endSec?: number;
    subLang?: string;
    embedSub?: boolean;
  }): Promise<string>;
  startAudioDownload(params: {
    url: string;
    bitrateKbps: number | "original";
    outputDir?: string;
    taskId?: string;
    startSec?: number;
    endSec?: number;
  }): Promise<string>;
  startAudioNativeDownload(params: {
    url: string;
    outputDir?: string;
    taskId?: string;
    startSec?: number;
    endSec?: number;
  }): Promise<string>;
  startThumbnailDownload(params: {
    url: string;
    outputDir?: string;
    taskId?: string;
  }): Promise<string>;
  cancelTask(taskId: string): Promise<void>;
  onProgress(callback: (p: TaskProgress) => void): () => void;
  pickOutputDir(): Promise<string | null>;
  openFolder(path?: string): Promise<void>;
}

type QtSignal<T> = { connect(callback: (payload: T) => void): void };

type QtVideoInfoPayload = {
  requestId: string;
  payload: VideoInfo | { error: string };
};

type QtBridge = {
  onProgressSignal?: QtSignal<string>;
  onVideoInfoSignal?: QtSignal<string | QtVideoInfoPayload>;
  fetchVideoInfoAsync?(url: string): Promise<string> | string;
  startVideoDownload(params: string): Promise<string> | string;
  startAudioDownload(params: string): Promise<string> | string;
  startAudioNativeDownload?(params: string): Promise<string> | string;
  startThumbnailDownload?(params: string): Promise<string> | string;
  cancelTask(taskId: string): Promise<void> | void;
  pickOutputDir(): Promise<string> | string;
  openFolder?(path: string): Promise<void> | void;
};

type QtChannel = { objects: { qtBridge?: QtBridge } };

type QtChannelConstructor = new (
  transport: object,
  callback: (channel: QtChannel) => void,
) => QtChannel;

declare global {
  interface Window {
    qtBridge?: QtBridge;
    QWebChannel?: QtChannelConstructor;
    qt?: { webChannelTransport?: object };
    __onVideoInfoReady?: (
      requestId: string,
      payload: VideoInfo | { error: string },
    ) => void;
    __onTaskProgress?: (progress: TaskProgress) => void;
  }
}

/* ------------------------------------------------------------------ */
/* Listener setup & QWebChannel initialization                         */
/* ------------------------------------------------------------------ */

const listeners = new Set<(p: TaskProgress) => void>();
let qtBridgeInstance: QtBridge | null = null;
const pendingInfo = new Map<
  string,
  { resolve: (info: VideoInfo) => void; reject: (error: Error) => void }
>();
const completedInfo = new Map<
  string,
  { value: VideoInfo | Error; ts: number }
>();
let channelInitStarted = false;
const INFO_REQUEST_TIMEOUT_MS = 60_000;
const COMPLETED_INFO_TTL_MS = 60_000;

const lastEmitMap = new Map<
  string,
  { status: string; percent: number; ts: number }
>();

function emit(p: TaskProgress) {
  if (!p || !p.taskId) return;
  const now = Date.now();
  const prev = lastEmitMap.get(p.taskId);
  const isTerminal =
    p.status === "DONE" || p.status === "ERROR" || p.status === "CANCELLED";
  if (
    !isTerminal &&
    prev &&
    prev.status === p.status &&
    Math.abs(prev.percent - (p.percent || 0)) < 0.5 &&
    now - prev.ts < 80
  ) {
    return;
  }
  lastEmitMap.set(p.taskId, {
    status: p.status,
    percent: p.percent || 0,
    ts: now,
  });
  listeners.forEach((l) => l(p));
}

function resolveVideoInfo(payload: string | QtVideoInfoPayload) {
  if (!payload) return;
  console.log("[JS-BRIDGE] resolveVideoInfo RECEIVED payload:", payload);
  try {
    const data = (
      typeof payload === "string" ? JSON.parse(payload) : payload
    ) as QtVideoInfoPayload;
    if (!data || !data.requestId || !data.payload) return;
    console.log(
      "[JS-BRIDGE] resolveVideoInfo parsed requestId=",
      data.requestId,
    );
    const result =
      typeof data.payload === "object" && "error" in data.payload
        ? new Error(data.payload.error)
        : (data.payload as VideoInfo);
    const pending = pendingInfo.get(data.requestId);
    if (pending) {
      console.log(
        "[JS-BRIDGE] Found pending info promise for req=",
        data.requestId,
      );
      pendingInfo.delete(data.requestId);
      if (result instanceof Error) pending.reject(result);
      else pending.resolve(result);
      return;
    }
    console.log(
      "[JS-BRIDGE] NO pending promise found for req=",
      data.requestId,
      "saving to completedInfo",
    );
    completedInfo.set(data.requestId, { value: result, ts: Date.now() });
  } catch (error) {
    console.error("Failed to parse video info signal", error);
  }
}

function pruneCompletedInfo() {
  const now = Date.now();
  for (const [key, entry] of completedInfo) {
    if (now - entry.ts > COMPLETED_INFO_TTL_MS) completedInfo.delete(key);
  }
}

function connectQtSignals(instance: QtBridge | null) {
  console.log(
    "[JS-BRIDGE] connectQtSignals called, instance exists=",
    !!instance,
  );
  if (!instance) return;
  if (instance.onProgressSignal) {
    console.log("[JS-BRIDGE] Connecting onProgressSignal");
    instance.onProgressSignal.connect((payload: string) => {
      try {
        emit(typeof payload === "string" ? JSON.parse(payload) : payload);
      } catch (error) {
        console.error("Failed to parse progress signal", error);
      }
    });
  }
  if (instance.onVideoInfoSignal) {
    console.log("[JS-BRIDGE] Connecting onVideoInfoSignal");
    instance.onVideoInfoSignal.connect(resolveVideoInfo);
  }
}

if (typeof window !== "undefined") {
  const setupChannel = () => {
    console.log(
      "[JS-BRIDGE] setupChannel called. channelInitStarted=",
      channelInitStarted,
      "window.qtBridge=",
      !!window.qtBridge,
      "hasQWC=",
      !!(window.QWebChannel && window.qt?.webChannelTransport),
    );
    if (channelInitStarted) return;
    if (window.qtBridge) {
      channelInitStarted = true;
      qtBridgeInstance = window.qtBridge;
      connectQtSignals(qtBridgeInstance);
    } else if (window.QWebChannel && window.qt?.webChannelTransport) {
      channelInitStarted = true;
      console.log("[JS-BRIDGE] Initializing new QWebChannel...");
      new window.QWebChannel(window.qt.webChannelTransport, (channel) => {
        console.log("[JS-BRIDGE] QWebChannel init callback fired!");
        qtBridgeInstance = channel.objects.qtBridge ?? null;
        if (qtBridgeInstance) window.qtBridge = qtBridgeInstance;
        connectQtSignals(qtBridgeInstance);
      });
    }
  };

  setupChannel();
  window.addEventListener("DOMContentLoaded", setupChannel);
  window.addEventListener("load", setupChannel);
}

async function ensureQtBridge(): Promise<QtBridge | null> {
  if (qtBridgeInstance) return qtBridgeInstance;
  if (typeof window === "undefined") return null;
  if (!window.qt?.webChannelTransport && !window.qtBridge) return null;

  for (let i = 0; i < 30; i++) {
    if (qtBridgeInstance) return qtBridgeInstance;
    if (window.qtBridge) {
      qtBridgeInstance = window.qtBridge;
      connectQtSignals(qtBridgeInstance);
      return qtBridgeInstance;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  return qtBridgeInstance;
}

// Attach direct JS window handlers for dual-path delivery from PySide6
if (typeof window !== "undefined") {
  window.__onVideoInfoReady = (
    requestId: string,
    payload: VideoInfo | { error: string },
  ) => {
    console.log(
      "[JS-BRIDGE] __onVideoInfoReady CALLED via runJavaScript req=",
      requestId,
    );
    resolveVideoInfo({ requestId, payload });
  };
  window.__onTaskProgress = (progress: TaskProgress) => {
    emit(progress);
  };
}

// Periodically clean up stale completed info entries
if (typeof window !== "undefined") {
  setInterval(pruneCompletedInfo, 30_000);
}

/* ------------------------------------------------------------------ */
/* Mock fallback internals                                            */
/* ------------------------------------------------------------------ */

const timers = new Map<string, ReturnType<typeof setInterval>>();
const cancelled = new Set<string>();
let seq = 0;
const newId = () => `task_${Date.now().toString(36)}_${(seq += 1)}`;
const thumb = (seed: string, w = 640, h = 360) =>
  `https://picsum.photos/seed/${seed}/${w}/${h}`;

/** Generate a unique client-side task ID. */
export const createTaskId = newId;

const BASE_FORMATS: VideoFormat[] = [
  {
    resolutionLabel: "2160p",
    height: 2160,
    fps: 60,
    vcodec: "vp9",
    acodec: "opus",
    estimatedSizeMB: 2480,
  },
  {
    resolutionLabel: "1440p",
    height: 1440,
    fps: 60,
    vcodec: "vp9",
    acodec: "opus",
    estimatedSizeMB: 1240,
  },
  {
    resolutionLabel: "1080p",
    height: 1080,
    fps: 60,
    vcodec: "avc1.640028",
    acodec: "mp4a.40.2",
    estimatedSizeMB: 620,
  },
  {
    resolutionLabel: "720p",
    height: 720,
    fps: 30,
    vcodec: "avc1.4d401f",
    acodec: "mp4a.40.2",
    estimatedSizeMB: 310,
  },
  {
    resolutionLabel: "480p",
    height: 480,
    fps: 30,
    vcodec: "avc1.4d401e",
    acodec: "mp4a.40.2",
    estimatedSizeMB: 145,
  },
  {
    resolutionLabel: "360p",
    height: 360,
    fps: 30,
    vcodec: "avc1.42001e",
    acodec: "mp4a.40.2",
    estimatedSizeMB: 78,
  },
];

/* ------------------------------------------------------------------ */
/* Bridge Implementation Export                                       */
/* ------------------------------------------------------------------ */

export const bridge: Bridge = {
  async fetchVideoInfo(url) {
    const qb = await ensureQtBridge();
    if (qb?.fetchVideoInfoAsync) {
      const requestId = await qb.fetchVideoInfoAsync(url);
      const reqId =
        typeof requestId === "string" ? requestId : String(requestId);

      return new Promise<VideoInfo>((resolve, reject) => {
        const timer = setTimeout(() => {
          pendingInfo.delete(reqId);
          reject(new Error("Quá thời gian chờ phản hồi từ yt-dlp (30 giây)."));
        }, INFO_REQUEST_TIMEOUT_MS);

        pendingInfo.set(reqId, {
          resolve: (info) => {
            clearTimeout(timer);
            resolve(info);
          },
          reject: (err) => {
            clearTimeout(timer);
            reject(err);
          },
        });

        const existing = completedInfo.get(reqId);
        if (existing) {
          completedInfo.delete(reqId);
          pendingInfo.delete(reqId);
          clearTimeout(timer);
          if (existing.value instanceof Error) reject(existing.value);
          else resolve(existing.value);
        }
      });
    }

    // Mock fallback
    await new Promise((r) => setTimeout(r, 800));
    const playlist = /[?&]list=/i.test(url);
    const seed = encodeURIComponent(url.slice(-12) || "origin");
    return {
      title: playlist
        ? "Signal Sessions — tuyển tập bản thu"
        : "Signal Sessions #14 — studio live",
      thumbnailUrl: thumb(seed, 960, 540),
      durationSec: playlist ? 743 : 512,
      channel: "Origin Broadcast",
      isPlaylist: playlist,
      formats: BASE_FORMATS,
    };
  },

  async startVideoDownload(params) {
    const id = params.taskId || newId();
    const qb = await ensureQtBridge();
    if (qb) {
      const res = await qb.startVideoDownload(
        JSON.stringify({ ...params, taskId: id }),
      );
      const str = typeof res === "string" ? res : String(res);
      if (str.startsWith("{")) {
        const parsed = JSON.parse(str);
        if (parsed.error) throw new Error(parsed.error);
        return parsed.taskId || id;
      }
      return id;
    }
    runMockTask(id, {
      kind: "video",
      label: `${params.height}p`,
      url: params.url,
    });
    return id;
  },

  async startAudioDownload(params) {
    const id = params.taskId || newId();
    const qb = await ensureQtBridge();
    if (qb) {
      const res = await qb.startAudioDownload(
        JSON.stringify({ ...params, taskId: id }),
      );
      const str = typeof res === "string" ? res : String(res);
      if (str.startsWith("{")) {
        const parsed = JSON.parse(str);
        if (parsed.error) throw new Error(parsed.error);
        return parsed.taskId || id;
      }
      return id;
    }
    runMockTask(id, {
      kind: "audio",
      label: `${params.bitrateKbps}kbps`,
      url: params.url,
    });
    return id;
  },

  async startAudioNativeDownload(params) {
    const id = params.taskId || newId();
    const qb = await ensureQtBridge();
    if (qb?.startAudioNativeDownload) {
      const res = await qb.startAudioNativeDownload(
        JSON.stringify({ ...params, taskId: id }),
      );
      const str = typeof res === "string" ? res : String(res);
      if (str.startsWith("{")) {
        const parsed = JSON.parse(str);
        if (parsed.error) throw new Error(parsed.error);
        return parsed.taskId || id;
      }
      return id;
    }
    runMockTask(id, { kind: "audio", label: "original", url: params.url });
    return id;
  },

  async startThumbnailDownload(params) {
    const id = params.taskId || newId();
    const qb = await ensureQtBridge();
    if (qb?.startThumbnailDownload) {
      const res = await qb.startThumbnailDownload(
        JSON.stringify({ ...params, taskId: id }),
      );
      const str = typeof res === "string" ? res : String(res);
      if (str.startsWith("{")) {
        const parsed = JSON.parse(str);
        if (parsed.error) throw new Error(parsed.error);
        return parsed.taskId || id;
      }
      return id;
    }
    runMockTask(id, { kind: "video", label: "Ảnh Bìa Gốc", url: params.url });
    return id;
  },

  async cancelTask(taskId) {
    const qb = await ensureQtBridge();
    if (qb) {
      return await qb.cancelTask(taskId);
    }
    cancelled.add(taskId);
    const t = timers.get(taskId);
    if (t) clearInterval(t);
    timers.delete(taskId);
    emit({ taskId, status: "CANCELLED", percent: 0 });
  },

  async pickOutputDir() {
    const qb = await ensureQtBridge();
    if (qb) {
      const dir = await qb.pickOutputDir();
      // Python returns "" when the user cancels QFileDialog; normalize to
      // null to match the documented Promise<string | null> contract.
      return dir || null;
    }
    return "C:\\Users\\User\\Downloads\\Origin";
  },

  async openFolder(path?: string) {
    const qb = await ensureQtBridge();
    if (qb?.openFolder) {
      await qb.openFolder(path || "");
    }
  },

  onProgress(callback) {
    listeners.add(callback);
    return () => listeners.delete(callback);
  },
};

export const taskKinds = new Map<string, "video" | "audio">();
export const taskLabels = new Map<string, string>();

function runMockTask(
  taskId: string,
  meta: { kind: "video" | "audio"; label: string; url: string },
) {
  taskKinds.set(taskId, meta.kind);
  taskLabels.set(taskId, meta.label);

  emit({ taskId, status: "QUEUED", percent: 0 });
  let percent = 0;

  setTimeout(() => {
    if (cancelled.has(taskId)) return;
    emit({ taskId, status: "FETCHING_INFO", percent: 0 });

    const timer = setInterval(() => {
      if (cancelled.has(taskId)) {
        clearInterval(timer);
        return;
      }

      percent += 5.0;
      if (percent >= 100) {
        clearInterval(timer);
        timers.delete(taskId);
        emit({ taskId, status: "MERGING", percent: 100 });
        setTimeout(() => {
          if (!cancelled.has(taskId))
            emit({ taskId, status: "DONE", percent: 100 });
        }, 800);
        return;
      }

      emit({
        taskId,
        status: "DOWNLOADING",
        percent: Math.round(percent),
        speedKBs: 3500,
        etaSec: Math.max(1, Math.round((100 - percent) / 5)),
      });
    }, 400);

    timers.set(taskId, timer);
  }, 500);
}
