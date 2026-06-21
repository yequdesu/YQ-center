import { getConfig } from "./client";
import type { SseEvent } from "./types";

export interface EventStreamHandlers {
  onEvent: (event: SseEvent) => void;
  onError?: (error: Error) => void;
  onClose?: () => void;
}

export function createEventStream(
  path: string,
  body: Record<string, unknown>,
  handlers: EventStreamHandlers,
): { abort: () => void } {
  const cfg = getConfig();
  const controller = new AbortController();

  void (async () => {
    try {
      const response = await fetch(`${cfg.baseUrl}${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${cfg.token}`,
          "Content-Type": "application/json; charset=utf-8",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Stream failed: ${response.status}`);
      }
      if (!response.body) {
        throw new Error("Stream response has no body");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary = buffer.indexOf("\n\n");
        while (boundary !== -1) {
          const frame = buffer.slice(0, boundary).trim();
          buffer = buffer.slice(boundary + 2);
          const dataLine = frame
            .split(/\r?\n/)
            .find((line) => line.startsWith("data:"));
          if (dataLine) {
            const payload = dataLine.slice(5).trim();
            if (payload) {
              handlers.onEvent(JSON.parse(payload) as SseEvent);
            }
          }
          boundary = buffer.indexOf("\n\n");
        }
      }
      handlers.onClose?.();
    } catch (error) {
      if (controller.signal.aborted) {
        handlers.onClose?.();
        return;
      }
      handlers.onError?.(error instanceof Error ? error : new Error(String(error)));
    }
  })();

  return {
    abort: () => controller.abort(),
  };
}
