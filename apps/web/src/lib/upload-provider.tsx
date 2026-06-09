"use client";

import { createClient } from "@/lib/supabase/client";
import { api, uploadFile, type ExtraSource } from "@/lib/api";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type UploadTaskStatus = "queued" | "uploading" | "registering" | "completed" | "failed" | "canceled";

export interface UploadTask {
  taskId: string;
  projectId: string;
  files: { name: string; size: number }[];
  status: UploadTaskStatus;
  progress: number;
  error: string | null;
  uploadedSources: ExtraSource[];
  startedAt: string;
  completedAt: string | null;
}

interface UploadContextValue {
  tasks: UploadTask[];
  startUpload: (projectId: string, files: File[]) => string;
  cancelUpload: (taskId: string) => void;
  getProjectTask: (projectId: string) => UploadTask | undefined;
}

const UploadContext = createContext<UploadContextValue | null>(null);
const ACTIVE_STATUSES = new Set<UploadTaskStatus>(["queued", "uploading", "registering"]);

function createTaskId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function mergeSources(existing: ExtraSource[], incoming: ExtraSource[]): ExtraSource[] {
  const byKey = new Map<string, ExtraSource>();
  for (const source of existing) byKey.set(source.r2_key, source);
  for (const source of incoming) byKey.set(source.r2_key, source);
  return Array.from(byKey.values());
}

export function UploadProvider({ children }: { children: ReactNode }) {
  const supabase = createClient();
  const [tasks, setTasks] = useState<UploadTask[]>([]);
  const controllers = useRef(new Map<string, AbortController>());

  const patchTask = useCallback((taskId: string, patch: Partial<UploadTask>) => {
    setTasks((prev) => prev.map((task) => (task.taskId === taskId ? { ...task, ...patch } : task)));
  }, []);

  const runUpload = useCallback(
    async (taskId: string, projectId: string, files: File[]) => {
      const controller = new AbortController();
      controllers.current.set(taskId, controller);
      patchTask(taskId, { status: "uploading", progress: 0 });

      try {
        const {
          data: { session },
        } = await supabase.auth.getSession();
        if (!session) throw new Error("로그인이 필요합니다");

        const totalSize = files.reduce((sum, file) => sum + file.size, 0);
        let prevUploaded = 0;
        const uploadedSources: ExtraSource[] = [];

        for (const file of files) {
          const baseUploaded = prevUploaded;
          const r2Key = await uploadFile(session.access_token, file, {
            signal: controller.signal,
            onProgress: (loaded) => {
              const progress = totalSize > 0 ? Math.round(((baseUploaded + loaded) / totalSize) * 100) : 0;
              patchTask(taskId, { progress });
            },
          });
          prevUploaded += file.size;
          const source = { r2_key: r2Key, filename: file.name, size_bytes: file.size };
          uploadedSources.push(source);
          patchTask(taskId, { uploadedSources: [...uploadedSources] });
        }

        patchTask(taskId, { status: "registering", progress: 100 });
        const latestProject = await api.getProject(session.access_token, projectId);
        await api.updateExtraSources(
          session.access_token,
          projectId,
          mergeSources(latestProject.extra_sources || [], uploadedSources)
        );
        patchTask(taskId, {
          status: "completed",
          progress: 100,
          completedAt: new Date().toISOString(),
        });
      } catch (error) {
        const aborted = controller.signal.aborted;
        patchTask(taskId, {
          status: aborted ? "canceled" : "failed",
          error: aborted ? "업로드가 취소되었습니다" : error instanceof Error ? error.message : "업로드에 실패했습니다",
          completedAt: new Date().toISOString(),
        });
      } finally {
        controllers.current.delete(taskId);
      }
    },
    [patchTask, supabase]
  );

  const startUpload = useCallback(
    (projectId: string, files: File[]) => {
      const taskId = createTaskId();
      const task: UploadTask = {
        taskId,
        projectId,
        files: files.map((file) => ({ name: file.name, size: file.size })),
        status: "queued",
        progress: 0,
        error: null,
        uploadedSources: [],
        startedAt: new Date().toISOString(),
        completedAt: null,
      };
      setTasks((prev) => [task, ...prev]);
      void runUpload(taskId, projectId, files);
      return taskId;
    },
    [runUpload]
  );

  const cancelUpload = useCallback(
    (taskId: string) => {
      controllers.current.get(taskId)?.abort();
      patchTask(taskId, {
        status: "canceled",
        error: "업로드가 취소되었습니다",
        completedAt: new Date().toISOString(),
      });
    },
    [patchTask]
  );

  const getProjectTask = useCallback(
    (projectId: string) => tasks.find((task) => task.projectId === projectId && ACTIVE_STATUSES.has(task.status)),
    [tasks]
  );

  useEffect(() => {
    const hasActive = tasks.some((task) => ACTIVE_STATUSES.has(task.status));
    if (!hasActive) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [tasks]);

  const value = useMemo(
    () => ({ tasks, startUpload, cancelUpload, getProjectTask }),
    [tasks, startUpload, cancelUpload, getProjectTask]
  );

  return <UploadContext.Provider value={value}>{children}</UploadContext.Provider>;
}

export function useUploads() {
  const context = useContext(UploadContext);
  if (!context) throw new Error("useUploads must be used inside UploadProvider");
  return context;
}

export function GlobalUploadBar() {
  const { tasks, cancelUpload } = useUploads();
  const activeTask = tasks.find((task) => ACTIVE_STATUSES.has(task.status));
  if (!activeTask) return null;

  const label =
    activeTask.status === "registering"
      ? "멀티캠 소스 등록 중"
      : activeTask.status === "queued"
        ? "업로드 대기 중"
        : "멀티캠 소스 업로드 중";

  return (
    <div className="fixed inset-x-0 bottom-0 z-[80] border-t border-white/10 bg-[#050816]/95 px-4 py-3 text-white shadow-2xl backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3 text-xs">
            <span className="truncate text-gray-300">
              {label} · {activeTask.files.length}개 파일
            </span>
            <span className="shrink-0 text-gray-500">{activeTask.progress}%</span>
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10">
            <div
              className="h-full rounded-full bg-cyan-400 transition-all duration-300"
              style={{ width: `${Math.min(100, Math.max(0, activeTask.progress))}%` }}
            />
          </div>
        </div>
        <button
          onClick={() => cancelUpload(activeTask.taskId)}
          className="shrink-0 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-gray-300 transition hover:bg-white/10 hover:text-white"
        >
          취소
        </button>
      </div>
    </div>
  );
}
