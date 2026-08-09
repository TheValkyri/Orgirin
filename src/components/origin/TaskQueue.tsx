import { useEffect, useRef } from "react";
import {
  X,
  RotateCcw,
  Music4,
  Film,
  CheckCircle2,
  AlertTriangle,
  Folder,
} from "lucide-react";
import { bridge, type TaskStatus, type TaskProgress } from "@/lib/bridge";
import { STATUS_LABEL, explainError, fmtEta, fmtSpeed } from "@/lib/format";
import { Waveform } from "./Waveform";
import { SpeedMeter } from "./SpeedMeter";
import { cn } from "@/lib/utils";

export interface QueueTask extends TaskProgress {
  kind: "video" | "audio";
  label: string;
  title: string;
  outputDir?: string;
}

const dot: Record<TaskStatus, string> = {
  QUEUED: "bg-muted-foreground",
  FETCHING_INFO: "bg-warning",
  DOWNLOADING: "bg-primary",
  MERGING: "bg-accent",
  DONE: "bg-success",
  ERROR: "bg-destructive",
  CANCELLED: "bg-muted-foreground",
};

function playDoneChime() {
  try {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(523.25, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(659.25, ctx.currentTime + 0.12);
    gain.gain.setValueAtTime(0.12, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.28);
    setTimeout(() => {
      ctx.close().catch(() => {});
    }, 350);
  } catch {
    // Ignore autoplay restriction
  }
}

export function TaskQueue({
  tasks,
  outputDir,
  onCancel,
  onRetry,
  onDismiss,
}: {
  tasks: QueueTask[];
  outputDir?: string;
  onCancel: (id: string) => void;
  onRetry: (task: QueueTask) => void;
  onDismiss: (id: string) => void;
}) {
  const prevDoneRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const currentDone = new Set(
      tasks.filter((t) => t.status === "DONE").map((t) => t.taskId),
    );
    let newDoneFound = false;
    currentDone.forEach((id) => {
      if (!prevDoneRef.current.has(id)) {
        newDoneFound = true;
      }
    });
    if (newDoneFound) {
      playDoneChime();
    }
    prevDoneRef.current = currentDone;
  }, [tasks]);

  const totalSpeedKBs = tasks.reduce(
    (sum, t) => sum + (t.status === "DOWNLOADING" ? t.speedKBs || 0 : 0),
    0,
  );
  const activeCount = tasks.filter(
    (t) =>
      t.status === "DOWNLOADING" ||
      t.status === "MERGING" ||
      t.status === "FETCHING_INFO",
  ).length;
  const doneCount = tasks.filter((t) => t.status === "DONE").length;

  return (
    <aside className="panel flex h-full flex-col rounded-2xl p-4">
      <header className="flex items-center justify-between pb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-foreground">
            Hàng đợi
          </h2>
          <span className="num rounded-md bg-panel-raised px-2 py-0.5 text-[11px] text-muted-foreground">
            {tasks.length.toString().padStart(2, "0")}
          </span>
        </div>
        <button
          type="button"
          onClick={() => void bridge.openFolder(outputDir)}
          title="Mở thư mục lưu tệp"
          className="flex items-center gap-1 rounded-md border border-border/80 bg-panel-raised/50 px-2 py-1 text-[11px] font-medium text-foreground transition-all hover:border-red-500 hover:text-red-400 active:scale-95 cursor-pointer"
        >
          <Folder className="h-3.5 w-3.5 text-red-500" />
          <span>Mở thư mục</span>
        </button>
      </header>

      {tasks.length > 0 && (
        <SpeedMeter
          totalSpeedKBs={totalSpeedKBs}
          activeCount={activeCount}
          doneCount={doneCount}
        />
      )}

      {tasks.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border px-4 py-10 text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-full bg-panel-raised">
            <Film className="h-5 w-5 text-muted-foreground" />
          </div>
          <p className="text-sm text-foreground">Chưa có tác vụ nào</p>
          <p className="text-xs leading-relaxed text-muted-foreground">
            Dán liên kết, chọn một mốc chất lượng rồi bấm Tải xuống. Tiến độ sẽ
            hiện ở đây.
          </p>
        </div>
      ) : (
        <div className="flex-1 space-y-3 overflow-y-auto pr-1">
          {tasks.map((t) => {
            const running =
              t.status === "DOWNLOADING" ||
              t.status === "MERGING" ||
              t.status === "QUEUED" ||
              t.status === "FETCHING_INFO";
            return (
              <article
                key={t.taskId}
                className="rounded-xl border border-border bg-panel-raised/60 p-3"
                style={{ animation: "rise .4s cubic-bezier(.22,1,.36,1) both" }}
              >
                <div className="flex items-start gap-2">
                  <span
                    className={cn(
                      "mt-1.5 h-2 w-2 shrink-0 rounded-full",
                      dot[t.status],
                      running && "animate-pulse-soft",
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[13px] font-medium text-foreground">
                      {t.title}
                    </p>
                    <p className="num mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                      {t.kind === "audio" ? (
                        <Music4 className="h-3 w-3" />
                      ) : (
                        <Film className="h-3 w-3" />
                      )}
                      {t.label} · {STATUS_LABEL[t.status]}
                    </p>
                  </div>

                  {t.status === "ERROR" ? (
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => onRetry(t)}
                        aria-label="Bắt đầu lại tác vụ"
                        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-panel hover:text-foreground"
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => onDismiss(t.taskId)}
                        aria-label="Xóa khỏi danh sách"
                        className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-panel hover:text-foreground"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ) : t.status === "DONE" ? (
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() =>
                          void bridge.openFolder(t.outputDir || outputDir)
                        }
                        title="Mở thư mục chứa file"
                        className="flex items-center gap-1 rounded-md bg-green-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-success transition-all hover:bg-green-500/20 active:scale-95 cursor-pointer"
                      >
                        <CheckCircle2 className="h-3.5 w-3.5 text-success" />
                        <span>Mở</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => onDismiss(t.taskId)}
                        aria-label="Xóa khỏi danh sách"
                        className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-panel hover:text-foreground cursor-pointer"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ) : t.status === "CANCELLED" ? (
                    <button
                      type="button"
                      onClick={() => onDismiss(t.taskId)}
                      aria-label="Xóa khỏi danh sách"
                      className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-panel hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => {
                        onCancel(t.taskId);
                        onDismiss(t.taskId);
                      }}
                      aria-label="Huỷ tác vụ"
                      className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-panel hover:text-destructive cursor-pointer"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>

                {t.status !== "ERROR" && t.status !== "CANCELLED" && (
                  <div className="mt-3">
                    {t.kind === "audio" ? (
                      <Waveform percent={t.percent} active={running} />
                    ) : (
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-panel">
                        <div
                          className="h-full rounded-full bg-signal transition-[width] duration-500 ease-out"
                          style={{ width: `${t.percent}%` }}
                        />
                      </div>
                    )}
                    <div className="num mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
                      <span>{t.percent}%</span>
                      {t.status === "DOWNLOADING" ? (
                        <span>
                          {fmtSpeed(t.speedKBs)} · còn {fmtEta(t.etaSec)}
                        </span>
                      ) : (
                        <span>{STATUS_LABEL[t.status]}</span>
                      )}
                    </div>
                  </div>
                )}

                {t.status === "ERROR" && (
                  <div className="mt-3 rounded-lg border border-destructive/35 bg-destructive/10 p-2.5">
                    <p className="flex items-center gap-1.5 text-[11px] font-semibold text-destructive">
                      <AlertTriangle className="h-3.5 w-3.5" /> Tác vụ dừng ở{" "}
                      {t.percent}%
                    </p>
                    <p className="mt-1.5 text-[11px] leading-relaxed text-foreground">
                      {explainError(t.errorMessage).cause}
                    </p>
                    <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                      {explainError(t.errorMessage).action}
                    </p>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}
    </aside>
  );
}
