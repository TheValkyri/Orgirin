export function fmtDuration(sec: number) {
  if (!sec || isNaN(sec) || sec < 0) return "0:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function fmtSize(mb: number) {
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${Math.round(mb)} MB`;
}

export function fmtSpeed(kbs?: number) {
  if (!kbs) return "—";
  return kbs >= 1024
    ? `${(kbs / 1024).toFixed(1)} MB/s`
    : `${Math.round(kbs)} KB/s`;
}

export function fmtEta(sec?: number) {
  if (sec == null) return "—";
  return sec >= 60 ? `${Math.floor(sec / 60)}m ${sec % 60}s` : `${sec}s`;
}

export const STATUS_LABEL: Record<string, string> = {
  QUEUED: "Đang chờ",
  FETCHING_INFO: "Đang đọc thông tin",
  DOWNLOADING: "Đang tải",
  MERGING: "Đang ghép luồng",
  DONE: "Hoàn tất",
  ERROR: "Thất bại",
  CANCELLING: "Đang huỷ…",
  CANCELLED: "Đã huỷ",
};

export function cleanErrorString(m: string): string {
  return (
    m
      // eslint-disable-next-line no-control-regex
      .replace(/\x1B\[[0-9;]*[a-zA-Z]/g, "")
      .replace(/\[0;3\d+m/g, "")
      .replace(/\[0m/g, "")
      .trim()
  );
}

/** Turns a raw backend error into cause + suggested action. */
export function explainError(message?: string): {
  cause: string;
  action: string;
} {
  const m = cleanErrorString(message ?? "");
  if (/403|Forbidden/i.test(m))
    return {
      cause:
        "Tác vụ bị giới hạn kết nối tạm thời từ máy chủ YouTube (HTTP 403 Forbidden).",
      action:
        "Vui lòng đợi vài phút rồi bấm thử lại tác vụ. Hệ thống đã giãn cách kết nối để né giới hạn.",
    };
  if (/ECONNRESET|ngắt|timeout|network|mạng/i.test(m))
    return {
      cause: m,
      action:
        "Kiểm tra kết nối rồi bắt đầu lại tác vụ — phần đã tải được giữ lại.",
    };
  if (/EACCES|quyền|permission|ghi/i.test(m))
    return {
      cause: m,
      action: "Chọn thư mục đích khác hoặc cấp quyền ghi cho thư mục hiện tại.",
    };
  if (/riêng tư|private/i.test(m))
    return {
      cause: m,
      action: "Dùng liên kết công khai hoặc unlisted của video.",
    };
  if (/khu vực|geo|region/i.test(m))
    return {
      cause: m,
      action: "Thử lại từ kết nối ở quốc gia được phép phát video.",
    };
  if (/disk full|Đĩa bị đầy|not enough space|no space left|ENOSPC/i.test(m))
    return {
      cause: m,
      action: "Giải phóng dung lượng ổ đĩa rồi bắt đầu lại tác vụ.",
    };
  return {
    cause: m || "Tác vụ dừng do lỗi không xác định.",
    action: "Bắt đầu lại tác vụ để thử lần nữa.",
  };
}
