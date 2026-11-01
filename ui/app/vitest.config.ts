import { defineConfig } from "vitest/config";

// Unit tests for pure UI logic (table model, lens nav). Component/DOM tests can be layered
// in later with jsdom; today we test framework-agnostic logic in Node.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
