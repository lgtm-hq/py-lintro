import { z } from "astro/zod";
import { DOC_CATEGORIES } from "../data/docs-nav";

export { DOC_CATEGORIES };

export const docsFrontmatterSchema = z.object({
  title: z.string().min(1),
  description: z.string().default(""),
  category: z.enum(DOC_CATEGORIES),
  order: z.number().default(100),
  navTitle: z.string().optional(),
  navGroup: z.string().optional(),
  sidebar: z.boolean().default(true),
  toc: z.boolean().default(true),
  draft: z.boolean().default(false),
  prev: z.string().optional(),
  next: z.string().optional(),
});

export type DocsFrontmatter = z.infer<typeof docsFrontmatterSchema>;
