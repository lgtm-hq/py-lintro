import { TOOLS } from './tools-catalog';
import {
  CONFIG_GROUPS,
  DEFAULT_BUILDER,
  buildConfigurationWithTools,
  type BuilderOptions,
} from '../lib/config-builder-core';

export { CONFIG_GROUPS, DEFAULT_BUILDER, type BuilderOptions };

export const AVAILABLE_TOOL_NAMES: readonly string[] = TOOLS.map((tool) => tool.name);

export function buildConfiguration(options: BuilderOptions): string {
  return buildConfigurationWithTools(options, AVAILABLE_TOOL_NAMES);
}
