const BUILTIN_PUBLIC_PROJECT_IDS = new Set([
  "3d2587aa-f65a-4746-a454-30bba7611ddc",
  "b094cf1c-bf9b-49f1-8a45-c646e3734692",
  "c26fee48-be40-4a0b-b660-a995a723a533",
  "296ec362-a5eb-405d-a839-3f65509a3ace",
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
