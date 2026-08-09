import type { VideoFormat } from "@/lib/bridge";
import { fmtSize } from "@/lib/format";
import { cn } from "@/lib/utils";

export function QualityLadder({
  formats,
  selectedHeights,
  onToggleHeight,
  onSelectAll,
  disabled,
  mode = "best",
}: {
  formats: VideoFormat[];
  selectedHeights: number[];
  onToggleHeight: (height: number) => void;
  onSelectAll?: () => void;
  disabled?: boolean;
  mode?: "best" | "compat" | "audio_native" | "mp3";
}) {
  const sorted = [...formats].sort((a, b) => b.height - a.height);
  const allSelected =
    sorted.length > 0 && selectedHeights.length === sorted.length;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground font-medium">
          Mẹo: Có thể chọn nhiều mốc cùng lúc để tải đồng thời. (
          {selectedHeights.length}/{sorted.length} đã chọn)
        </span>
        {onSelectAll && (
          <button
            type="button"
            onClick={onSelectAll}
            className="rounded-lg border border-border bg-panel-raised/50 px-2.5 py-1 text-[11px] font-semibold text-foreground transition-all hover:border-primary active:scale-95"
          >
            {allSelected ? "Bỏ chọn tất cả" : "Chọn tất cả mốc"}
          </button>
        )}
      </div>

      <div className="grid gap-2.5 sm:grid-cols-2">
        {sorted.map((f, i) => {
          const active = selectedHeights.includes(f.height);
          const is4k = f.height >= 2160;
          const is2k = f.height >= 1440 && f.height < 2160;
          const isFhd = f.height >= 1080 && f.height < 1440;
          const is60fps = f.fps >= 50;

          const isCompatMode = mode === "compat";
          const displaySizeMB =
            isCompatMode && f.estimatedSizeCompatMB
              ? f.estimatedSizeCompatMB
              : f.estimatedSizeMB;
          const displayCodec = isCompatMode
            ? "H.264"
            : f.vcodec.split(".")[0].toUpperCase();

          const badgeLabel = is4k
            ? "4K Ultra HD"
            : is2k
              ? "2K QHD"
              : isFhd
                ? "Full HD"
                : f.height >= 720
                  ? "HD"
                  : "SD";

          return (
            <button
              key={`${f.height}-${f.fps}-${f.vcodec}-${i}`}
              type="button"
              disabled={disabled}
              onClick={() => onToggleHeight(f.height)}
              aria-pressed={active}
              className={cn(
                "group relative flex items-center justify-between overflow-hidden rounded-xl border p-3.5 text-left transition-all duration-300 cursor-pointer",
                "hover:-translate-y-0.5 hover:border-primary/60 active:scale-[0.97]",
                active
                  ? "border-red-500 bg-panel-raised shadow-[0_0_20px_rgba(255,0,0,0.3)] ring-1 ring-red-500/50"
                  : "border-border/80 bg-panel/50 hover:bg-panel-raised/50",
                disabled && "pointer-events-none opacity-45",
              )}
              style={{
                animation: `rise 0.4s cubic-bezier(.22,1,.36,1) ${i * 35}ms both`,
              }}
            >
              <div className="flex items-center gap-3 min-w-0">
                <div
                  className={cn(
                    "num flex h-10 w-12 shrink-0 flex-col items-center justify-center rounded-lg text-xs font-bold transition-all duration-300",
                    active
                      ? "bg-red-600 text-white shadow-md shadow-red-950/40"
                      : "bg-panel-raised text-foreground group-hover:bg-red-500/20 group-hover:text-red-400",
                  )}
                >
                  <span>{f.height}p</span>
                  {is60fps && (
                    <span className="text-[9px] font-normal opacity-90">
                      60fps
                    </span>
                  )}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="truncate text-sm font-semibold text-foreground">
                      {f.resolutionLabel}
                    </span>
                    <span
                      className={cn(
                        "rounded px-1.5 py-0.5 text-[9px] font-medium tracking-wide uppercase",
                        is4k || is2k
                          ? "bg-red-500/20 text-red-400 border border-red-500/30"
                          : isFhd
                            ? "bg-red-600/15 text-red-400 border border-red-500/30"
                            : "bg-muted/40 text-muted-foreground",
                      )}
                    >
                      {badgeLabel}
                    </span>
                  </div>
                  <p className="num mt-1 truncate text-[11px] text-muted-foreground">
                    {displayCodec} · {f.fps}fps
                  </p>
                </div>
              </div>

              <div className="num shrink-0 text-right">
                <span className="block text-xs font-semibold text-foreground">
                  ~{fmtSize(displaySizeMB)}
                </span>
                <span className="block text-[10px] text-muted-foreground">
                  ước tính
                </span>
              </div>

              {/* Checkbox indicator */}
              <div className="ml-2.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border border-border transition-all">
                {active && <span className="h-2 w-2 rounded-sm bg-red-500" />}
              </div>

              {/* Neon accent bar */}
              <span
                className={cn(
                  "pointer-events-none absolute bottom-0 left-0 h-[2px] w-full bg-gradient-to-r from-red-600 to-orange-500 transition-all duration-300",
                  active ? "opacity-100" : "opacity-0 group-hover:opacity-40",
                )}
              />
            </button>
          );
        })}
      </div>
    </div>
  );
}
