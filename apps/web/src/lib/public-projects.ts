const BUILTIN_PUBLIC_PROJECT_IDS = new Set([
  "3d2587aa-f65a-4746-a454-30bba7611ddc",
]);

function configuredPublicProjectIds(): Set<string> {
  return new Set(
    (process.env.NEXT_PUBLIC_PUBLIC_PROJECT_IDS || "")
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean)
  );
}

export function isPublicProjectId(projectId: string | null | undefined): boolean {
  if (!projectId) return false;
  return BUILTIN_PUBLIC_PROJECT_IDS.has(projectId) || configuredPublicProjectIds().has(projectId);
}

export function isPublicProjectPath(pathname: string): boolean {
  const match = pathname.match(/^\/projects\/([^/]+)(?:\/review)?\/?$/);
  return isPublicProjectId(match?.[1]);
}
