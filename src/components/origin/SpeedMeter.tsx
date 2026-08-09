import { Activity, ArrowDown, Zap } from "lucide-react";
import { fmtSpeed } from "@/lib/format";
import { cn } from "@/lib/utils";

export function SpeedMeter({
  totalSpeedKBs,
  activeCount,
  doneCount,
}: {
  totalSpeedKBs: number;
  activeCount: number;
  doneCount: number;
}) {
  const isDownloading = activeCount > 0;

  return (
    <div className="mb-3 rounded-xl border border-border/80 bg-panel-raised/40 p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="relative flex h-7 w-7 items-center justify-center rounded-lg bg-red-500/15 text-red-500">
            <Activity
              className={cn("h-4 w-4", isDownloading && "animate-pulse")}
            />
            {isDownloading && (
              <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-red-500 animate-ping" />
            )}
          </div>
          <div>
            <span className="block text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
              Băng thông tổng
            </span>
            <span className="num text-sm font-bold text-white flex items-center gap-1">
              {isDownloading ? (
                <>
                  <ArrowDown className="h-3.5 w-3.5 text-red-500 animate-bounce" />
                  {totalSpeedKBs > 0
                    ? fmtSpeed(totalSpeedKBs)
                    : "Đang xử lý..."}
                </>
              ) : (
                <span className="text-muted-foreground font-normal text-xs">
                  Sẵn sàng
                </span>
              )}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2 num text-[11px]">
          {isDownloading && (
            <span className="flex items-center gap-1 rounded-md bg-red-500/20 px-2 py-1 font-semibold text-red-400 border border-red-500/30">
              <Zap className="h-3 w-3 fill-red-400" />
              {activeCount} luồng đang tải
            </span>
          )}
          {doneCount > 0 && !isDownloading && (
            <span className="rounded-md bg-green-500/15 px-2 py-1 font-semibold text-success border border-green-500/20">
              Đã xong {doneCount} tệp
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
