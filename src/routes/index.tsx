import { createFileRoute } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  Link2,
  Loader2,
  Music4,
  Download,
  AlertTriangle,
  Radio,
  Clipboard,
  Scissors,
  Subtitles,
  Image as ImageIcon,
  Sparkles,
} from "lucide-react";
import {
  bridge,
  createTaskId,
  type TaskProgress,
  type VideoInfo,
} from "@/lib/bridge";
import { fmtDuration, explainError } from "@/lib/format";
import { QualityLadder } from "@/components/origin/QualityLadder";
import { TaskQueue, type QueueTask } from "@/components/origin/TaskQueue";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Origin — Tải video & audio YouTube chất lượng tốt nhất" },
      {
        name: "description",
        content:
          "Origin đọc mọi mốc chất lượng của liên kết YouTube, cho bạn chọn độ phân giải tốt nhất (MKV/MP4) hoặc âm thanh (Audio gốc/MP3), và theo dõi tiến độ tải theo thời gian thực.",
      },
      {
        property: "og:title",
        content: "Origin — Tải video & audio YouTube chất lượng tốt nhất",
      },
      {
        property: "og:description",
        content:
          "Thang chất lượng đầy đủ, video MKV gốc / MP4 tương thích, audio gốc không chuyển mã hoặc MP3, hàng đợi tải với tiến độ và tốc độ theo thời gian thực.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: OriginApp,
});

type AudioBitrate = 128 | 192 | 256 | 320 | "original";

type DownloadMode = "best" | "compat" | "audio_native" | "mp3";

type DownloadMeta = {
  kind: "video" | "audio";
  mode: DownloadMode;
  label: string;
  title: string;
  sourceUrl: string;
  height?: number;
  bitrateKbps?: AudioBitrate;
  outputDir?: string;
  startSec?: number;
  endSec?: number;
  subLang?: string;
  embedSub?: boolean;
};

const BITRATES: AudioBitrate[] = ["original", 128, 192, 256, 320];

const youtubeWatchUrl = (id: string) => `https://www.youtube.com/watch?v=${id}`;

function parseTimeToSec(str: string): number | undefined {
  if (!str || !str.trim()) return undefined;
  const s = str.trim();
  if (s.includes(":")) {
    const parts = s.split(":").map((p) => parseInt(p, 10) || 0);
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  }
  const val = parseInt(s, 10);
  return isNaN(val) ? undefined : val;
}

function OriginApp() {
  const [url, setUrl] = useState("");
  const [info, setInfo] = useState<VideoInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [infoError, setInfoError] = useState<string | null>(null);

  const [mode, setMode] = useState<DownloadMode>("best");
  const [selectedHeights, setSelectedHeights] = useState<number[]>([]);
  const [selectedBitrates, setSelectedBitrates] = useState<AudioBitrate[]>([
    "original",
  ]);
  const [selectedEntries, setSelectedEntries] = useState<string[]>([]);

  // Section 3: Trim & Subtitle States
  const [enableTrim, setEnableTrim] = useState(false);
  const [startTimeStr, setStartTimeStr] = useState("00:00");
  const [endTimeStr, setEndTimeStr] = useState("");
  const [subLang, setSubLang] = useState<string>("none");
  const [embedSub, setEmbedSub] = useState(false);

  const toggleHeight = useCallback((h: number) => {
    setSelectedHeights((prev) =>
      prev.includes(h)
        ? prev.length > 1
          ? prev.filter((x) => x !== h)
          : prev
        : [...prev, h],
    );
  }, []);

  const selectAllHeights = useCallback(() => {
    if (!info) return;
    const all = info.formats.map((f) => f.height);
    setSelectedHeights((prev) =>
      prev.length === all.length ? [Math.max(...all)] : all,
    );
  }, [info]);

  const toggleBitrate = useCallback((b: AudioBitrate) => {
    setSelectedBitrates((prev) =>
      prev.includes(b)
        ? prev.length > 1
          ? prev.filter((x) => x !== b)
          : prev
        : [...prev, b],
    );
  }, []);

  const selectAllBitrates = useCallback(() => {
    setSelectedBitrates((prev) =>
      prev.length === BITRATES.length ? ["original"] : [...BITRATES],
    );
  }, []);

  const [tasks, setTasks] = useState<QueueTask[]>([]);
  const metaRef = useRef(new Map<string, DownloadMeta>());

  useEffect(() => {
    return bridge.onProgress((p: TaskProgress) => {
      setTasks((prev) => {
        const meta = metaRef.current.get(p.taskId);
        const idx = prev.findIndex((t) => t.taskId === p.taskId);
        const next: QueueTask = {
          ...p,
          kind: meta?.kind ?? "video",
          label: meta?.label ?? "—",
          title: meta?.title ?? "Tác vụ tải",
          outputDir: meta?.outputDir,
        };
        if (idx === -1) {
          const updated = [next, ...prev];
          if (updated.length > 50) {
            const evicted = updated.slice(50);
            evicted.forEach((t) => metaRef.current.delete(t.taskId));
            return updated.slice(0, 50);
          }
          return updated;
        }
        const copy = [...prev];
        copy[idx] = { ...copy[idx], ...next };
        return copy;
      });
    });
  }, []);

  const fetchInfo = useCallback(async () => {
    setLoading(true);
    setInfoError(null);
    setInfo(null);
    try {
      const res = await bridge.fetchVideoInfo(url);
      setInfo(res);
      const topHeight = res.formats.length
        ? Math.max(...res.formats.map((f) => f.height))
        : 1080;
      setSelectedHeights([topHeight]);
      setSelectedEntries(res.playlistEntries?.map((e) => e.id) ?? []);
    } catch (e) {
      setInfoError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [url]);

  const [outputDir, setOutputDir] = useState<string | null>(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("origin_saved_output_dir") || null;
    }
    return null;
  });

  const downloadOne = useCallback(
    async (
      taskId: string,
      sourceUrl: string,
      meta: Omit<DownloadMeta, "sourceUrl">,
    ) => {
      const outDir = outputDir || undefined;
      metaRef.current.set(taskId, { ...meta, sourceUrl, outputDir: outDir });

      setTasks((prev) => {
        const next: QueueTask = {
          taskId,
          status: "QUEUED",
          percent: 0,
          kind: meta.kind,
          label: meta.label,
          title: meta.title,
          outputDir: outDir,
        };
        const idx = prev.findIndex((t) => t.taskId === taskId);
        if (idx === -1) return [next, ...prev];
        return prev;
      });

      const startSec = enableTrim ? parseTimeToSec(startTimeStr) : undefined;
      const endSec = enableTrim ? parseTimeToSec(endTimeStr) : undefined;
      const selectedSub = subLang !== "none" ? subLang : undefined;

      metaRef.current.set(taskId, {
        ...meta,
        sourceUrl,
        outputDir: outDir,
        startSec,
        endSec,
        subLang: selectedSub,
        embedSub,
      });

      try {
        if (meta.mode === "mp3") {
          await bridge.startAudioDownload({
            url: sourceUrl,
            bitrateKbps: meta.bitrateKbps ?? "original",
            outputDir: outDir,
            taskId,
            startSec,
            endSec,
          });
          return taskId;
        }

        if (meta.mode === "audio_native") {
          await bridge.startAudioNativeDownload({
            url: sourceUrl,
            outputDir: outDir,
            taskId,
            startSec,
            endSec,
          });
          return taskId;
        }

        await bridge.startVideoDownload({
          url: sourceUrl,
          height: meta.height ?? 0,
          outputFormat: meta.mode === "compat" ? "compat" : "best",
          outputDir: outDir,
          taskId,
          startSec,
          endSec,
          subLang: selectedSub,
          embedSub,
        });
        return taskId;
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : String(err);
        setTasks((prev) =>
          prev.map((t) =>
            t.taskId === taskId
              ? { ...t, status: "ERROR", errorMessage: errMsg, percent: 0 }
              : t,
          ),
        );
        throw err;
      }
    },
    [outputDir, enableTrim, startTimeStr, endTimeStr, subLang, embedSub],
  );

  const handleDownloadThumbnail = useCallback(async () => {
    if (!info) return;
    const taskId = createTaskId();
    const title = `${info.title} (Ảnh Bìa Gốc)`;
    const outDir = outputDir || undefined;

    metaRef.current.set(taskId, {
      kind: "video",
      mode: "best",
      label: "Ảnh Bìa Gốc",
      title,
      sourceUrl: url,
      outputDir: outDir,
    });

    setTasks((prev) => [
      {
        taskId,
        status: "QUEUED",
        percent: 0,
        kind: "video",
        label: "Ảnh Bìa Gốc",
        title,
        outputDir: outDir,
      },
      ...prev,
    ]);

    try {
      await bridge.startThumbnailDownload({
        url,
        outputDir: outDir,
        taskId,
      });
    } catch (e) {
      const errMsg = e instanceof Error ? e.message : String(e);
      setTasks((prev) =>
        prev.map((t) =>
          t.taskId === taskId
            ? { ...t, status: "ERROR", errorMessage: errMsg, percent: 0 }
            : t,
        ),
      );
      setDownloadError(errMsg);
    }
  }, [info, outputDir, url]);

  const handlePickOutputDir = async () => {
    const dir = await bridge.pickOutputDir();
    if (dir) {
      setOutputDir(dir);
      localStorage.setItem("origin_saved_output_dir", dir);
    }
  };

  const [downloadError, setDownloadError] = useState<string | null>(null);

  const startDownload = useCallback(async () => {
    if (!info) return;

    const title = info.title;
    setDownloadError(null);

    const isVideoMode = mode === "best" || mode === "compat";
    const kind: "video" | "audio" = isVideoMode ? "video" : "audio";

    try {
      if (info.isPlaylist && info.playlistEntries) {
        const targets = info.playlistEntries.filter((entry) =>
          selectedEntries.includes(entry.id),
        );

        if (targets.length === 0) {
          setDownloadError(
            "Chọn ít nhất một video trong playlist trước khi tải.",
          );
          return;
        }

        const heightsToRun = isVideoMode ? selectedHeights : [0];
        const bitratesToRun =
          mode === "mp3" ? selectedBitrates : ["original" as AudioBitrate];
        const playlistErrors: string[] = [];

        for (const entry of targets) {
          const sourceUrl = youtubeWatchUrl(entry.id);
          for (const h of heightsToRun) {
            for (const br of bitratesToRun) {
              const label = (() => {
                switch (mode) {
                  case "best":
                    return `${h}p · MKV`;
                  case "compat":
                    return `${h}p · MP4`;
                  case "audio_native":
                    return "Audio gốc";
                  case "mp3":
                    return br === "original"
                      ? "MP3 · VBR tối đa"
                      : `MP3 · ${br}kbps`;
                }
              })();

              const taskId = createTaskId();
              try {
                await downloadOne(taskId, sourceUrl, {
                  kind,
                  mode,
                  label,
                  title: entry.title,
                  height: isVideoMode ? h : undefined,
                  bitrateKbps: mode === "mp3" ? br : undefined,
                });
              } catch (e) {
                playlistErrors.push(
                  `${entry.title}: ${e instanceof Error ? e.message : String(e)}`,
                );
              }
            }
          }
        }
        if (playlistErrors.length > 0) {
          setDownloadError(
            `Có ${playlistErrors.length} tác vụ playlist gặp lỗi khi khởi tạo: ${playlistErrors.join("; ")}`,
          );
        }
        return;
      }

      const heightsToRun = isVideoMode ? selectedHeights : [0];
      const bitratesToRun =
        mode === "mp3" ? selectedBitrates : ["original" as AudioBitrate];
      for (const h of heightsToRun) {
        for (const br of bitratesToRun) {
          const label = (() => {
            switch (mode) {
              case "best":
                return `${h}p · MKV`;
              case "compat":
                return `${h}p · MP4`;
              case "audio_native":
                return "Audio gốc";
              case "mp3":
                return br === "original"
                  ? "MP3 · VBR tối đa"
                  : `MP3 · ${br}kbps`;
            }
          })();

          const taskId = createTaskId();
          await downloadOne(taskId, url, {
            kind,
            mode,
            label,
            title,
            height: isVideoMode ? h : undefined,
            bitrateKbps: mode === "mp3" ? br : undefined,
          });
        }
      }
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : String(e));
    }
  }, [
    downloadOne,
    selectedHeights,
    selectedBitrates,
    info,
    mode,
    selectedEntries,
    url,
  ]);

  const retry = useCallback(
    async (task: QueueTask) => {
      const meta = metaRef.current.get(task.taskId);
      const sourceUrl = meta?.sourceUrl || url;
      const retryMode = meta?.mode || "best";
      const retryHeight = meta?.height ?? (selectedHeights[0] || 1080);
      const curBitrate = selectedBitrates[0] || "original";
      const retryBitrate = meta?.bitrateKbps ?? curBitrate;
      const isVideoMode = retryMode === "best" || retryMode === "compat";
      const kind: "video" | "audio" = isVideoMode ? "video" : "audio";

      const retryLabel = (() => {
        switch (retryMode) {
          case "best":
            return `${retryHeight ?? 0}p · MKV`;
          case "compat":
            return `${retryHeight ?? 0}p · MP4`;
          case "audio_native":
            return "Audio gốc";
          case "mp3":
            return retryBitrate === "original"
              ? "MP3 · VBR tối đa"
              : `MP3 · ${retryBitrate}kbps`;
        }
      })();

      setDownloadError(null);
      try {
        // Remove the failed task from the UI before creating a new one
        setTasks((prev) => prev.filter((t) => t.taskId !== task.taskId));
        metaRef.current.delete(task.taskId);

        const id = createTaskId();
        metaRef.current.set(id, {
          kind,
          mode: retryMode,
          label: retryLabel,
          title: meta?.title || task.title,
          sourceUrl,
          height: isVideoMode ? retryHeight : undefined,
          bitrateKbps: retryMode === "mp3" ? retryBitrate : undefined,
          startSec: meta?.startSec,
          endSec: meta?.endSec,
          subLang: meta?.subLang,
          embedSub: meta?.embedSub,
        });

        if (retryMode === "mp3") {
          await bridge.startAudioDownload({
            url: sourceUrl,
            bitrateKbps: retryBitrate,
            outputDir: outputDir || undefined,
            taskId: id,
            startSec: meta?.startSec,
            endSec: meta?.endSec,
          });
        } else if (retryMode === "audio_native") {
          await bridge.startAudioNativeDownload({
            url: sourceUrl,
            outputDir: outputDir || undefined,
            taskId: id,
            startSec: meta?.startSec,
            endSec: meta?.endSec,
          });
        } else {
          await bridge.startVideoDownload({
            url: sourceUrl,
            height: retryHeight ?? 0,
            outputFormat: retryMode === "compat" ? "compat" : "best",
            outputDir: outputDir || undefined,
            taskId: id,
            startSec: meta?.startSec,
            endSec: meta?.endSec,
            subLang: meta?.subLang,
            embedSub: meta?.embedSub,
          });
        }
      } catch (e) {
        setDownloadError(e instanceof Error ? e.message : String(e));
      }
    },
    [url, selectedBitrates, selectedHeights, outputDir],
  );

  const readyToDownload = useMemo(() => {
    if (!info) return false;
    if (info.isPlaylist) return selectedEntries.length > 0;
    const isVideoMode = mode === "best" || mode === "compat";
    if (isVideoMode) return selectedHeights.length > 0;
    if (mode === "mp3") return selectedBitrates.length > 0;
    return true;
  }, [
    info,
    mode,
    selectedHeights.length,
    selectedBitrates.length,
    selectedEntries.length,
  ]);

  const [downloadingAnim, setDownloadingAnim] = useState(false);

  const triggerDownloadWithAnim = useCallback(() => {
    setDownloadingAnim(true);
    setTimeout(() => setDownloadingAnim(false), 550);
    void startDownload();
  }, [startDownload]);

  const [isDragging, setIsDragging] = useState(false);

  const handlePasteFromClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text && text.trim()) {
        setUrl(text.trim());
      }
    } catch {
      // Permission denied or unavailable
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const text = e.dataTransfer.getData("text");
    if (text && text.trim()) {
      setUrl(text.trim());
    }
  };

  const isTikTok = info?.platform === "tiktok";
  const isPhotoPost =
    info?.mediaType === "photo" || (isTikTok && info?.isPlaylist);

  useEffect(() => {
    if (isTikTok && (mode === "compat" || mode === "mp3")) {
      setMode("best");
    }
  }, [isTikTok, mode]);

  const modeOptions = isTikTok
    ? ([
        {
          id: "best" as const,
          label: "Video Tốt Nhất",
          sub: "Chọn stream TikTok khả dụng từ metadata nguồn",
        },
        {
          id: "audio_native" as const,
          label: "Audio Gốc",
          sub: "Tải Nhạc Nền TikTok",
        },
      ] as const)
    : ([
        {
          id: "best" as const,
          label: "Chất lượng cao nhất",
          sub: "MKV (AV1/VP9)",
        },
        { id: "compat" as const, label: "MP4 tương thích", sub: "H.264 / AAC" },
        { id: "audio_native" as const, label: "Audio gốc", sub: "m4a / opus" },
        { id: "mp3" as const, label: "Tải MP3", sub: "Transcode VBR" },
      ] as const);

  return (
    <div className="min-h-screen w-full px-4 py-5 sm:px-6 xl:px-8">
      <div className="grid w-full gap-6 xl:grid-cols-[minmax(0,1fr)_380px] 2xl:grid-cols-[minmax(0,1fr)_420px]">
        {/* Main Content Area */}
        <main className="space-y-5 min-w-0 flex-1">
          <header
            style={{ animation: "rise .5s cubic-bezier(.22,1,.36,1) both" }}
          >
            <p className="num flex items-center gap-2 text-xs uppercase tracking-[0.24em] text-red-500 font-semibold">
              <Radio className="h-3.5 w-3.5 text-red-500 animate-pulse" />{" "}
              YouTube Origin Studio
            </p>
            <h1 className="mt-2 text-3xl font-bold leading-tight sm:text-4xl text-white">
              Tải Video & Audio{" "}
              <span className="bg-gradient-to-r from-red-500 via-red-600 to-orange-500 bg-clip-text text-transparent">
                Chất Lượng Cao Nhất
              </span>
            </h1>
          </header>

          {/* Hero Input */}
          <section
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            className={cn(
              "panel rounded-2xl p-4 sm:p-5 shadow-lg transition-all duration-300",
              isDragging &&
                "border-2 border-dashed border-red-500 bg-red-500/10 scale-[1.01]",
            )}
            style={{
              animation: "rise .5s cubic-bezier(.22,1,.36,1) 80ms both",
            }}
          >
            <div className="flex flex-col gap-2.5 sm:flex-row">
              <div className="relative flex-1">
                <Link2 className="absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  id="yt-url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !loading) void fetchInfo();
                  }}
                  placeholder={
                    isDragging
                      ? "Thả liên kết YouTube hoặc TikTok vào đây..."
                      : "Dán hoặc kéo thả liên kết YouTube / TikTok tại đây..."
                  }
                  className="num h-12 w-full rounded-xl border border-input bg-background/60 pl-10 pr-24 text-sm text-foreground outline-none transition-all placeholder:text-muted-foreground/60 focus:border-primary focus:ring-2 focus:ring-primary/20"
                />
                <button
                  type="button"
                  onClick={() => void handlePasteFromClipboard()}
                  title="Dán từ bộ nhớ tạm (Clipboard)"
                  className="absolute right-2.5 top-1/2 flex -translate-y-1/2 items-center gap-1 rounded-lg border border-border bg-panel-raised px-2.5 py-1 text-xs font-semibold text-foreground transition-all hover:border-red-500 hover:text-red-400 active:scale-95 cursor-pointer"
                >
                  <Clipboard className="h-3.5 w-3.5 text-red-500" />
                  <span>Dán</span>
                </button>
              </div>
              <button
                type="button"
                onClick={() => void fetchInfo()}
                disabled={loading || !url.trim()}
                className="group inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-red-600 to-red-500 px-6 text-sm font-semibold text-white transition-all duration-200 hover:-translate-y-0.5 active:scale-95 disabled:pointer-events-none disabled:opacity-45 shadow-md shadow-red-950/40 cursor-pointer"
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {loading ? "Đang đọc..." : "Lấy thông tin"}
                {!loading && (
                  <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-1" />
                )}
              </button>
            </div>

            {infoError && (
              <div className="mt-3.5 rounded-xl border border-destructive/40 bg-destructive/10 p-3.5">
                <p className="flex items-center gap-2 text-xs font-semibold text-destructive">
                  <AlertTriangle className="h-4 w-4" /> Không đọc được liên kết
                </p>
                <p className="mt-1 text-xs leading-relaxed text-foreground">
                  {explainError(infoError).cause}
                </p>
                <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                  {explainError(infoError).action}
                </p>
              </div>
            )}

            {!info && !infoError && !loading && (
              <p className="num mt-2.5 text-[11px] text-muted-foreground">
                Gợi ý: Dán liên kết video đơn lẻ, YouTube Shorts, Playlist hoặc
                video TikTok.
              </p>
            )}
          </section>

          {info && (
            <div className="space-y-6">
              {/* Media Card Preview */}
              <section
                className="panel overflow-hidden rounded-2xl border border-border/80 bg-panel-raised/30 p-4 sm:p-5"
                style={{ animation: "rise .5s cubic-bezier(.22,1,.36,1) both" }}
              >
                <div className="flex flex-col gap-4 sm:flex-row items-center sm:items-start">
                  <div className="relative aspect-video w-full sm:w-60 shrink-0 overflow-hidden rounded-xl bg-background/80 shadow-md">
                    <img
                      src={info.thumbnailUrl}
                      alt={`Ảnh đại diện của ${info.title}`}
                      referrerPolicy="no-referrer"
                      loading="lazy"
                      className="h-full w-full object-cover transition-transform duration-500 hover:scale-105"
                    />
                    <span className="num absolute bottom-2 right-2 rounded bg-black/80 px-2 py-0.5 text-[10px] font-medium text-white backdrop-blur-sm">
                      {fmtDuration(info.durationSec)}
                    </span>
                  </div>

                  <div className="min-w-0 flex-1 space-y-2 text-center sm:text-left">
                    <h2
                      className="text-base font-bold leading-snug text-foreground sm:text-lg line-clamp-2 hover:line-clamp-none transition-all cursor-pointer"
                      title={info.title}
                    >
                      {info.title}
                    </h2>
                    <p className="text-xs font-medium text-muted-foreground">
                      {info.channel}
                    </p>
                    <div className="num flex flex-wrap justify-center sm:justify-start gap-1.5 text-[11px]">
                      <span className="rounded-md bg-panel-raised px-2.5 py-1 font-medium text-foreground">
                        {fmtDuration(info.durationSec)}
                      </span>
                      <span className="rounded-md bg-panel-raised px-2.5 py-1 text-muted-foreground">
                        {info.formats.length} luồng có sẵn
                      </span>
                      {info.isPlaylist && (
                        <span className="rounded-md bg-red-500/20 px-2.5 py-1 font-medium text-red-400 border border-red-500/30">
                          Playlist · {info.playlistEntries?.length ?? 0} mục
                        </span>
                      )}
                    </div>
                    <div className="pt-1 flex justify-center sm:justify-start">
                      <button
                        type="button"
                        onClick={() => void handleDownloadThumbnail()}
                        className="flex items-center gap-1.5 rounded-lg border border-border/80 bg-panel/70 px-3 py-1.5 text-xs font-medium text-foreground transition-all hover:border-red-500 hover:text-red-400 active:scale-95 cursor-pointer shadow-sm"
                      >
                        <ImageIcon className="h-3.5 w-3.5 text-red-500" />
                        <span>
                          {isTikTok && info.isPlaylist
                            ? "Tải Bộ Ảnh Gốc (N ảnh + Audio)"
                            : "Tải Ảnh Bìa Gốc (WebP/JPG)"}
                        </span>
                      </button>
                    </div>
                  </div>
                </div>
              </section>

              {/* Download Options Panel */}
              <section
                className="panel rounded-2xl p-4 sm:p-5 space-y-5"
                style={{
                  animation: "rise .5s cubic-bezier(.22,1,.36,1) 60ms both",
                }}
              >
                {/* Mode Grid Selection */}
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3">
                    Định dạng xuất
                  </h3>
                  <div
                    className={cn(
                      "grid gap-2.5",
                      isTikTok ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-4",
                    )}
                  >
                    {modeOptions.map((m) => (
                      <button
                        key={m.id}
                        type="button"
                        onClick={() => setMode(m.id)}
                        className={cn(
                          "relative rounded-xl border p-3 text-left transition-all duration-200 hover:-translate-y-0.5 active:scale-95 cursor-pointer",
                          mode === m.id
                            ? "border-red-500 bg-panel-raised text-foreground shadow-[0_0_20px_rgba(255,0,0,0.3)] ring-1 ring-red-500/50"
                            : "border-border/80 bg-panel/50 text-muted-foreground hover:bg-panel-raised/40",
                        )}
                      >
                        <span className="block text-xs font-bold">
                          {m.label}
                        </span>
                        <span className="mt-1 block text-[10px] text-muted-foreground/80">
                          {m.sub}
                        </span>
                        {mode === m.id && (
                          <span className="absolute top-2 right-2 h-2 w-2 rounded-full bg-red-500 shadow-sm shadow-red-500" />
                        )}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Dynamic Mode Details */}
                {isPhotoPost ? (
                  <div className="rounded-xl border border-border/80 bg-panel/40 p-4 space-y-2">
                    <h3 className="text-sm font-semibold text-foreground">
                      Bài viết Bộ Ảnh TikTok Carousel
                    </h3>
                    <p className="text-xs text-muted-foreground">
                      Tất cả ảnh thuộc bộ ảnh sẽ được tải xuống dạng HD gốc
                      (.jpg/.png) cùng nhạc nền bài viết nếu có.
                    </p>
                  </div>
                ) : mode === "best" || mode === "compat" ? (
                  <div>
                    <div className="flex items-center justify-between mb-2.5">
                      <h3 className="text-sm font-semibold text-foreground">
                        {isTikTok
                          ? "Chọn độ phân giải TikTok"
                          : "Chọn độ phân giải"}
                      </h3>
                      <span className="text-[11px] text-muted-foreground">
                        {isTikTok
                          ? "Chọn stream TikTok khả dụng từ metadata nguồn."
                          : mode === "best"
                            ? "Tự động chọn codec tốt nhất (AV1/VP9)"
                            : "Ép chọn H.264 tương thích mọi thiết bị"}
                      </span>
                    </div>
                    <QualityLadder
                      formats={info.formats}
                      selectedHeights={selectedHeights}
                      onToggleHeight={toggleHeight}
                      onSelectAll={selectAllHeights}
                      mode={mode}
                    />
                  </div>
                ) : mode === "mp3" ? (
                  <div className="rounded-xl border border-border/80 bg-panel/40 p-4">
                    <div className="flex items-center justify-between">
                      <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                        <Music4 className="h-4 w-4 text-red-500" /> Bitrate MP3
                      </h3>
                      <button
                        type="button"
                        onClick={selectAllBitrates}
                        className="rounded-lg border border-border bg-panel-raised/50 px-2.5 py-1 text-[11px] font-semibold text-foreground transition-all hover:border-primary active:scale-95 cursor-pointer"
                      >
                        {selectedBitrates.length === BITRATES.length
                          ? "Bỏ chọn tất cả"
                          : "Chọn tất cả"}
                      </button>
                    </div>
                    <p className="mt-1.5 text-[11px] text-muted-foreground">
                      Chọn nhiều mốc bitrate để tải đồng thời. (
                      {selectedBitrates.length}/{BITRATES.length} đã chọn)
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {BITRATES.map((b) => {
                        const active = selectedBitrates.includes(b);
                        return (
                          <button
                            key={String(b)}
                            type="button"
                            onClick={() => toggleBitrate(b)}
                            className={cn(
                              "num relative flex items-center gap-2 rounded-xl border px-3.5 py-2 text-xs font-semibold transition-all duration-200 hover:-translate-y-0.5 cursor-pointer",
                              active
                                ? "border-red-500 bg-panel-raised text-foreground shadow-[0_0_15px_rgba(255,0,0,0.3)] ring-1 ring-red-500/40"
                                : "border-border bg-panel/60 text-muted-foreground",
                            )}
                          >
                            {b === "original" ? "MP3 VBR Tối đa" : `${b}kbps`}
                            <span
                              className={cn(
                                "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border transition-all",
                                active
                                  ? "border-red-500 bg-red-500/20"
                                  : "border-border",
                              )}
                            >
                              {active && (
                                <span className="h-1.5 w-1.5 rounded-sm bg-red-500" />
                              )}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
                      Bitrate cao hơn nguồn không làm tăng chất lượng thực.
                      Khuyến nghị dùng &quot;MP3 VBR Tối đa&quot;.
                    </p>
                  </div>
                ) : (
                  <div className="rounded-xl border border-border/80 bg-panel/40 p-4">
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <Music4 className="h-4 w-4 text-red-500" />{" "}
                      {isTikTok
                        ? "Tải Nhạc Nền TikTok (Audio Gốc)"
                        : "Tải âm thanh gốc (Native Audio)"}
                    </h3>
                    <p className="mt-1.5 text-xs text-muted-foreground leading-relaxed">
                      {isTikTok
                        ? "Tải nguyên bản bài nhạc nền gốc trực tiếp từ video TikTok về (m4a/mp3) không suy hao chất lượng."
                        : "Tải nguyên bản file audio gốc (m4a hoặc webm/opus) trực tiếp từ YouTube. Không chuyển mã, Định dạng thực tế được hiển thị sau khi xác minh."}
                    </p>
                  </div>
                )}

                {/* Section 3: Time Trim Section */}
                <div className="rounded-xl border border-border/80 bg-panel/40 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <Scissors className="h-4 w-4 text-red-500" /> Cắt phân
                      cảnh (Trim Range)
                    </h3>
                    <label className="flex items-center gap-2 cursor-pointer text-xs text-muted-foreground">
                      <span>{enableTrim ? "Đã bật cắt" : "Tắt"}</span>
                      <input
                        type="checkbox"
                        checked={enableTrim}
                        onChange={(e) => setEnableTrim(e.target.checked)}
                        className="h-4 w-4 accent-red-500 cursor-pointer"
                      />
                    </label>
                  </div>
                  {enableTrim ? (
                    <div className="grid grid-cols-2 gap-3 pt-1">
                      <div>
                        <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                          Thời gian bắt đầu (MM:SS hoặc giây)
                        </label>
                        <input
                          type="text"
                          value={startTimeStr}
                          onChange={(e) => setStartTimeStr(e.target.value)}
                          placeholder="00:00"
                          className="num h-9 w-full rounded-lg border border-input bg-background/60 px-3 text-xs font-mono text-foreground outline-none focus:border-red-500"
                        />
                      </div>
                      <div>
                        <label className="block text-[11px] font-medium text-muted-foreground mb-1">
                          Thời gian kết thúc (MM:SS hoặc giây)
                        </label>
                        <input
                          type="text"
                          value={endTimeStr}
                          onChange={(e) => setEndTimeStr(e.target.value)}
                          placeholder={fmtDuration(info.durationSec)}
                          className="num h-9 w-full rounded-lg border border-input bg-background/60 px-3 text-xs font-mono text-foreground outline-none focus:border-red-500"
                        />
                      </div>
                    </div>
                  ) : (
                    <p className="text-[11px] text-muted-foreground">
                      Bật để chỉ tải đúng phân cảnh mong muốn thay vì toàn bộ
                      video/audio (tiết kiệm 80-90% dung lượng).
                    </p>
                  )}
                </div>

                {/* Section 3: Subtitles / Vietsub Section (Video modes only, if supported) */}
                {(mode === "best" || mode === "compat") &&
                  info?.capabilities?.supportsSubtitles !== false && (
                    <div className="rounded-xl border border-border/80 bg-panel/40 p-4 space-y-3">
                      <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
                        <Subtitles className="h-4 w-4 text-red-500" /> Phụ đề &
                        Vietsub
                      </h3>
                      <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
                        <select
                          value={subLang}
                          onChange={(e) => setSubLang(e.target.value)}
                          className="num h-9 rounded-lg border border-input bg-background/80 px-3 text-xs font-medium text-foreground outline-none focus:border-red-500 cursor-pointer"
                        >
                          <option value="none">Không tải phụ đề</option>
                          <option value="vi">
                            Tiếng Việt (Vietsub / Auto-Translate)
                          </option>
                          <option value="en">Tiếng Anh (English)</option>
                          <option value="all">Tất cả phụ đề có sẵn</option>
                        </select>

                        {subLang !== "none" && (
                          <label className="flex items-center gap-2 text-xs text-foreground cursor-pointer">
                            <input
                              type="checkbox"
                              checked={embedSub}
                              onChange={(e) => setEmbedSub(e.target.checked)}
                              className="h-4 w-4 accent-red-500 cursor-pointer"
                            />
                            <span>
                              Nhúng phụ đề trực tiếp vào video (MKV/MP4)
                            </span>
                          </label>
                        )}
                      </div>
                    </div>
                  )}

                {/* Saved Output Folder Bar */}
                <div className="flex items-center justify-between gap-3 rounded-xl border border-border/80 bg-panel-raised/40 p-3.5">
                  <div className="min-w-0 flex-1">
                    <span className="block text-[11px] font-medium text-muted-foreground">
                      Thư mục lưu (đã tự ghi nhớ)
                    </span>
                    <p className="num mt-0.5 truncate font-mono text-xs font-semibold text-foreground">
                      {outputDir || "Mặc định (Downloads/Origin Downloads)"}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handlePickOutputDir()}
                    className="shrink-0 rounded-lg border border-border bg-panel px-3 py-1.5 text-xs font-medium text-foreground transition-all hover:border-red-500 hover:bg-panel-raised active:scale-95 cursor-pointer"
                  >
                    Chọn thư mục khác
                  </button>
                </div>

                {/* Primary Download Action Button with Punchy Spring Animation */}
                <button
                  type="button"
                  onClick={triggerDownloadWithAnim}
                  disabled={!readyToDownload}
                  className={cn(
                    "relative inline-flex h-13 w-full items-center justify-center gap-2.5 rounded-xl text-sm font-bold text-white transition-all duration-300 shadow-lg cursor-pointer",
                    "bg-gradient-to-r from-red-600 via-red-500 to-red-600 shadow-red-950/50",
                    "hover:opacity-95 hover:shadow-[0_0_35px_rgba(255,0,0,0.5)] hover:scale-[1.008] active:scale-95",
                    downloadingAnim &&
                      "animate-punchy ring-2 ring-red-400 shadow-[0_0_45px_rgba(255,0,0,0.7)]",
                    !readyToDownload &&
                      "pointer-events-none opacity-40 shadow-none",
                  )}
                >
                  <Download
                    className={cn(
                      "h-5 w-5 transition-transform duration-300",
                      downloadingAnim
                        ? "animate-arrow-drop scale-125"
                        : "group-hover:translate-y-0.5",
                    )}
                  />
                  <span>
                    {(() => {
                      const isVideoMode = mode === "best" || mode === "compat";
                      const suffix =
                        mode === "best"
                          ? "MKV Gốc"
                          : mode === "compat"
                            ? "MP4 Tương thích"
                            : mode === "audio_native"
                              ? "Audio Gốc"
                              : "MP3 Transcode";
                      if (info?.isPlaylist) {
                        return `Tải xuống ${selectedEntries.length} tệp (${suffix})`;
                      }
                      if (isVideoMode && selectedHeights.length > 0) {
                        return selectedHeights.length === 1
                          ? `Tải xuống ${selectedHeights[0]}p · ${suffix}`
                          : `Tải xuống ${selectedHeights.length} mốc đã chọn · ${suffix}`;
                      }
                      if (isVideoMode) {
                        return "Vui lòng chọn ít nhất 1 mốc chất lượng";
                      }
                      if (mode === "mp3" && selectedBitrates.length > 1) {
                        return `Tải xuống ${selectedBitrates.length} mốc bitrate · ${suffix}`;
                      }
                      return `Tải xuống · ${suffix}`;
                    })()}
                  </span>
                </button>

                {downloadError && (
                  <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-destructive">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {downloadError}
                  </p>
                )}
              </section>

              {/* Playlist Section */}
              {info.isPlaylist && info.playlistEntries && (
                <section
                  className="panel rounded-2xl p-4 sm:p-5"
                  style={{
                    animation: "rise .5s cubic-bezier(.22,1,.36,1) 120ms both",
                  }}
                >
                  <div className="flex items-center justify-between pb-2">
                    <h3 className="text-sm font-semibold text-foreground">
                      Danh sách video Playlist
                      <span className="num ml-2 text-xs text-muted-foreground">
                        {selectedEntries.length}/{info.playlistEntries.length}{" "}
                        đã chọn
                      </span>
                    </h3>
                    <button
                      type="button"
                      onClick={() =>
                        setSelectedEntries((prev) =>
                          prev.length === info.playlistEntries!.length
                            ? []
                            : info.playlistEntries!.map((e) => e.id),
                        )
                      }
                      className="rounded-lg border border-border px-3 py-1.5 text-xs text-foreground transition-all hover:border-primary"
                    >
                      {selectedEntries.length === info.playlistEntries.length
                        ? "Bỏ chọn tất cả"
                        : "Chọn tất cả"}
                    </button>
                  </div>

                  <ul className="mt-3 divide-y divide-border/60 max-h-80 overflow-y-auto pr-1">
                    {info.playlistEntries.map((e, i) => {
                      const checked = selectedEntries.includes(e.id);
                      return (
                        <li key={e.id}>
                          <label className="flex cursor-pointer items-center gap-3 py-2.5 transition-colors hover:bg-panel-raised/40">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() =>
                                setSelectedEntries((prev) =>
                                  checked
                                    ? prev.filter((x) => x !== e.id)
                                    : [...prev, e.id],
                                )
                              }
                              className="h-4 w-4 shrink-0 accent-[var(--signal-cyan)]"
                            />
                            <span className="num w-5 shrink-0 text-[11px] text-muted-foreground">
                              {String(i + 1).padStart(2, "0")}
                            </span>
                            <img
                              src={e.thumbnailUrl}
                              alt=""
                              loading="lazy"
                              className="h-10 w-16 shrink-0 rounded-lg object-cover"
                            />
                            <span className="truncate text-xs font-medium text-foreground">
                              {e.title}
                            </span>
                          </label>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              )}
            </div>
          )}
        </main>

        {/* Task Queue Sidebar on the Right */}
        <aside className="h-full flex flex-col xl:sticky xl:top-5 xl:h-[calc(100vh-2.5rem)]">
          <TaskQueue
            tasks={tasks}
            outputDir={outputDir || undefined}
            onCancel={(id) => void bridge.cancelTask(id)}
            onRetry={retry}
            onDismiss={(id) => {
              setTasks((prev) => prev.filter((t) => t.taskId !== id));
              metaRef.current.delete(id);
            }}
          />
        </aside>
      </div>
    </div>
  );
}
