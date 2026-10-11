import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PLUGIN_ID, PLUGIN_NAME, PLUGIN_VERSION, plugin } from "../src/geolibre";
import { DEFAULT_STATE, normalizePluginState } from "../src/state";

describe("project state", () => {
  it("accepts the default state and rejects a bad length", () => {
    expect(normalizePluginState(DEFAULT_STATE)).toEqual(DEFAULT_STATE);
    expect(normalizePluginState({ ...DEFAULT_STATE, length: 3 })).toBeNull();
    expect(normalizePluginState({ ...DEFAULT_STATE, selectedCode: "nope" })).toBeNull();
    expect(normalizePluginState({ ...DEFAULT_STATE, selectedCode: "wg73jd" })?.selectedCode).toBe(
      "WG73JD",
    );
  });
});

describe("plugin manifest", () => {
  it("matches plugin.json", () => {
    const manifest = JSON.parse(
      readFileSync(new URL("../geolibre-plugin/plugin.json", import.meta.url), "utf8"),
    ) as { id: string; name: string; version: string };
    expect(plugin.id).toBe(manifest.id);
    expect(plugin.name).toBe(manifest.name);
    expect(plugin.version).toBe(manifest.version);
    expect(plugin.id).toBe(PLUGIN_ID);
    expect(plugin.name).toBe(PLUGIN_NAME);
    expect(plugin.version).toBe(PLUGIN_VERSION);
    expect(plugin.urlParameterNames).toEqual(["hkgeocode"]);
  });
});
