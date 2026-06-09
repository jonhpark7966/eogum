"use client";

import { GlobalUploadBar, UploadProvider } from "@/lib/upload-provider";
import type { ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  return (
    <UploadProvider>
      {children}
      <GlobalUploadBar />
    </UploadProvider>
  );
}
