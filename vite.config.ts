import { defineConfig } from "vite";
import viteReact from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";

// Origin — cấu hình Vite thuần, KHÔNG còn phụ thuộc gói riêng
// "@lovable.dev/vite-tanstack-config" (gói đó chỉ dùng được bên trong sandbox
// của Lovable — app desktop này không cần và không thể cài nó).
//
// Quyết định kiến trúc (xem PLAN.md mục 2): app chạy 100% client-side (không
// route nào dùng loader/createServerFn/beforeLoad), nên KHÔNG bật plugin
// nitro (server SSR) — build_static_ui.py chỉ lấy phần bundle JS/CSS tĩnh ở
// .output/public/assets rồi tự nhúng vào một index.html tối giản, phục vụ
// qua HTTP server nội bộ nhẹ trong shell/main.py. Không cần Node runtime nào
// đi kèm khi đóng gói bằng PyInstaller.
export default defineConfig({
  server: {
    host: "::",
    port: 8080,
  },
  resolve: {
    tsconfigPaths: true,
    alias: { "@": "/src" },
  },
  plugins: [tailwindcss(), tanstackStart(), viteReact()],
});
