import { staticFile } from "remotion";

// Solo HTTPS remoto; HTTP e' ammesso unicamente per i server locali di fiducia.
const isLoopbackHttp = (src: string): boolean =>
  /^http:\/\/(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/|$)/i.test(src);

const isRemoteAsset = (src: string): boolean =>
  src.startsWith("https://") ||
  isLoopbackHttp(src) ||
  src.startsWith("data:");

const isWindowsAbsolutePath = (src: string): boolean =>
  /^[A-Za-z]:[\\/]/.test(src);

/** Resolve public assets and absolute filesystem paths consistently. */
export function resolveAsset(src: string): string {
  if (src.startsWith("http://") && !isLoopbackHttp(src)) {
    throw new Error(`resolveAsset: URL non sicura (http), usare https: ${src}`);
  }
  if (isRemoteAsset(src)) {
    return src;
  }

  const withoutScheme = src.replace(/^file:\/\//i, "");
  const clean = /^\/[A-Za-z]:[\\/]/.test(withoutScheme)
    ? withoutScheme.slice(1)
    : withoutScheme;

  if (clean.startsWith("/") || isWindowsAbsolutePath(clean)) {
    const posix = clean.replace(/\\/g, "/");
    return posix.startsWith("/") ? `file://${posix}` : `file:///${posix}`;
  }

  return staticFile(clean);
}
