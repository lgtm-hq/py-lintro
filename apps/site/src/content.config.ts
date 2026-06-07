import { defineCollection } from "astro:content";
import { glob } from "astro/loaders";
import { docsFrontmatterSchema } from "./lib/docs-schema";

const docs = defineCollection({
  loader: glob({ pattern: "**/*.{md,mdx}", base: "./src/content/docs" }),
  schema: docsFrontmatterSchema,
});

export const collections = { docs };
