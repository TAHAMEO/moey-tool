import type { Metadata, Viewport } from "next";
import { AuthProvider } from "@/lib/auth";
import { ToastProvider } from "@/components/toast";
import "./globals.css";

export const metadata: Metadata = {
  title: "CLIPFORGE AI",
  description: "Turn long videos and livestreams into publish-ready Shorts and Reels.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#5b47e0" };

const themeScript = `try{var t=localStorage.getItem("cf-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-dvh antialiased">
        <ToastProvider>
          <AuthProvider>{children}</AuthProvider>
        </ToastProvider>
      </body>
    </html>
  );
}
