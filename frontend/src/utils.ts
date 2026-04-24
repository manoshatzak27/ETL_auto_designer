export function basename(path: string): string {
  return path.replace(/\\/g, '/').split('/').pop() || path
}
