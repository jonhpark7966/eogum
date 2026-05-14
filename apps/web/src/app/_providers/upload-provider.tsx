"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, uploadFile, type ExtraSource } from "@/lib/api";

type UploadStatus = "queued" | "uploading" | "done" | "error";

export type UploadJob = {
  id: string;
  projectId: string;
  filename: string;
  sizeBytes: number;
  progress: number;
  status: UploadStatus;
  error?: string;
  r2Key?: string;
};

type UploadContextValue = {
  jobs: UploadJob[];
  enqueueExtraSources: (projectId: string, files: File[], token: string) => void;
  jobsFor: (projectId: string) => UploadJob[];
  clearFinished: (projectId: string) => void;
};

const UploadContext = createContext<UploadContextValue | null>(null);

function updateJob(
  jobs: UploadJob[],
  jobId: string,
  patch: Partial<UploadJob>
): UploadJob[] {
  return jobs.map((job) => (job.id === jobId ? { ...job, ...patch } : job));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function UploadProvider({ children }: { children: ReactNode }) {
  const [jobs, setJobs] = useState<UploadJob[]>([]);

  const enqueueExtraSources = useCallback((projectId: string, files: File[], token: string) => {
    if (files.length === 0) return;

    const newJobs: UploadJob[] = files.map((file) => ({
      id: crypto.randomUUID(),
      projectId,
      filename: file.name,
      sizeBytes: file.size,
      progress: 0,
      status: "queued",
    }));

    setJobs((prev) => [...prev, ...newJobs]);

    void (async () => {
      const completed: ExtraSource[] = [];
      const completedJobIds: string[] = [];

      for (const [index, file] of files.entries()) {
        const job = newJobs[index];
        setJobs((prev) => updateJob(prev, job.id, { status: "uploading", progress: 0 }));

        try {
          const r2Key = await uploadFile(token, file, (loaded, total) => {
            const progress = Math.min(100, Math.round((loaded / total) * 100));
            setJobs((prev) => updateJob(prev, job.id, { progress }));
          });

          completed.push({
            r2_key: r2Key,
            filename: file.name,
            size_bytes: file.size,
          });
          completedJobIds.push(job.id);
          setJobs((prev) => updateJob(prev, job.id, { progress: 100, r2Key }));
        } catch (error) {
          setJobs((prev) =>
            updateJob(prev, job.id, {
              status: "error",
              error: errorMessage(error),
            })
          );
        }
      }

      if (completed.length === 0) return;

      try {
        const project = await api.getProject(token, projectId);
        await api.updateExtraSources(token, projectId, [
          ...project.extra_sources,
          ...completed,
        ]);
        setJobs((prev) =>
          prev.map((job) =>
            completedJobIds.includes(job.id) ? { ...job, status: "done", progress: 100 } : job
          )
        );
      } catch (error) {
        setJobs((prev) =>
          prev.map((job) =>
            completedJobIds.includes(job.id)
              ? { ...job, status: "error", error: errorMessage(error) }
              : job
          )
        );
      }
    })();
  }, []);

  const jobsFor = useCallback(
    (projectId: string) => jobs.filter((job) => job.projectId === projectId),
    [jobs]
  );

  const clearFinished = useCallback((projectId: string) => {
    setJobs((prev) =>
      prev.filter(
        (job) =>
          job.projectId !== projectId || (job.status !== "done" && job.status !== "error")
      )
    );
  }, []);

  useEffect(() => {
    const hasActiveUpload = jobs.some(
      (job) => job.status === "queued" || job.status === "uploading"
    );
    if (!hasActiveUpload) return;

    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };

    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [jobs]);

  const value = useMemo(
    () => ({ jobs, enqueueExtraSources, jobsFor, clearFinished }),
    [jobs, enqueueExtraSources, jobsFor, clearFinished]
  );

  return <UploadContext.Provider value={value}>{children}</UploadContext.Provider>;
}

export function useUploads() {
  const context = useContext(UploadContext);
  if (!context) {
    throw new Error("useUploads must be used within UploadProvider");
  }
  return context;
}

export function ProjectUploadStatus({
  projectId,
  className = "",
}: {
  projectId: string;
  className?: string;
}) {
  const { jobsFor, clearFinished } = useUploads();
  const jobs = jobsFor(projectId);

  if (jobs.length === 0) return null;

  const activeJobs = jobs.filter((job) => job.status === "queued" || job.status === "uploading");
  const failedJobs = jobs.filter((job) => job.status === "error");
  const doneJobs = jobs.filter((job) => job.status === "done");
  const totalBytes = jobs.reduce((sum, job) => sum + job.sizeBytes, 0);
  const uploadedBytes = jobs.reduce(
    (sum, job) => sum + job.sizeBytes * (job.progress / 100),
    0
  );
  const progress = totalBytes > 0 ? Math.round((uploadedBytes / totalBytes) * 100) : 0;

  return (
    <div className={`rounded-xl border border-cyan-500/20 bg-cyan-500/[0.04] px-4 py-3 ${className}`}>
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-cyan-200">
            멀티캠 업로드 {activeJobs.length > 0 ? "진행 중" : "완료"}
          </p>
          <p className="mt-0.5 text-xs text-gray-400">
            완료 {doneJobs.length} / 전체 {jobs.length}
            {failedJobs.length > 0 ? ` · 실패 ${failedJobs.length}` : ""}
          </p>
        </div>
        {activeJobs.length === 0 && (
          <button
            onClick={() => clearFinished(projectId)}
            className="shrink-0 text-xs text-gray-400 transition-colors hover:text-gray-200"
          >
            닫기
          </button>
        )}
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-violet-500 transition-all duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>
      <div className="mt-2 space-y-1">
        {jobs.map((job) => (
          <div key={job.id} className="flex items-center justify-between gap-3 text-xs">
            <span className="truncate text-gray-300">{job.filename}</span>
            <span
              className={
                job.status === "error"
                  ? "shrink-0 text-red-300"
                  : job.status === "done"
                    ? "shrink-0 text-emerald-300"
                    : "shrink-0 text-cyan-300"
              }
              title={job.error}
            >
              {job.status === "error"
                ? "실패"
                : job.status === "done"
                  ? "완료"
                  : `${job.progress}%`}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
