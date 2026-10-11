/** YAML text <-> editor model, shared by the YAML tab and its tests. */
import * as yaml from "js-yaml";
import type { DslConfig } from "./types";
import { parseDslConfig } from "./types";

export interface YamlError {
  message: string;
  line?: number | undefined;
}

export function serializeConfig(config: DslConfig): string {
  return yaml.dump(config, { indent: 2, lineWidth: 120, noRefs: true });
}

export function parseYamlToDsl(text: string): { config: DslConfig; error: YamlError | null } {
  try {
    // js-yaml 5 throws on empty input; keep the "must be a mapping" message for it.
    // js-yaml 5 throws on empty input; keep the "must be a mapping" message for it.
    const parsed = text.trim() === "" ? null : yaml.load(text);
    if (parsed === null || parsed === undefined || typeof parsed !== "object") {
      return {
        config: {} as DslConfig,
        error: { message: "YAML must be a mapping (object), not a scalar or array" },
      };
    }
    const config = parseDslConfig(parsed as Record<string, unknown>);
    return { config, error: null };
  } catch (e: unknown) {
    if (e instanceof yaml.YAMLException) {
      return {
        config: {} as DslConfig,
        error: {
          message: e.message,
          line: e.mark?.line !== undefined ? e.mark.line + 1 : undefined,
        },
      };
    }
    const msg = e instanceof Error ? e.message : "Unknown parse error";
    return { config: {} as DslConfig, error: { message: msg } };
  }
}
