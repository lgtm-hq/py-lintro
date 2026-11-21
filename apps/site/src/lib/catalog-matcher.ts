import type { CatalogTool, ToolCategory } from '../data/tool-categories';

export interface CatalogFilters {
  category: ToolCategory | 'all';
  query: string;
  fixOnly: boolean;
}

/** Search all tools, including dual-purpose tools in lint and format categories. */
export function matchesCatalog(tool: CatalogTool, filters: CatalogFilters): boolean {
  const query = filters.query.trim().toLocaleLowerCase();
  const category = filters.category;
  const categoryMatches =
    category === 'all' ||
    category === tool.category ||
    ((category === 'lint' || category === 'fmt') && tool.category === 'both');
  return (
    categoryMatches &&
    (!filters.fixOnly || tool.fixes) &&
    (!query || (tool.name + ' ' + tool.target).toLocaleLowerCase().includes(query))
  );
}
