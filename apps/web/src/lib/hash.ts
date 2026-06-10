import { createSHA256 } from "hash-wasm";

export async function sha256File(
  file: File,
  onProgress?: (loaded: number, total: number) => void
): Promise<string> {
  const chunkSize = 8 * 1024 * 1024;
  const hasher = await createSHA256();
  hasher.init();

  let offset = 0;
  while (offset < file.size) {
    const end = Math.min(offset + chunkSize, file.size);
    const chunk = new Uint8Array(await file.slice(offset, end).arrayBuffer());
    hasher.update(chunk);
    offset = end;
    onProgress?.(offset, file.size);
  }

  return hasher.digest("hex");
}
