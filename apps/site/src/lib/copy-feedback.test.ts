import { afterEach, describe, expect, it, vi } from 'vitest';
import { copyWithRecovery } from './copy-feedback';

interface CopyState {
  clipboard: string;
  selected: string;
  focused: boolean;
  tabIndex: number;
  status: { textContent: string };
}
function environment(text: string | null, available = true, existingTabIndex = false) {
  const state: CopyState = {
    clipboard: '',
    selected: '',
    focused: false,
    tabIndex: existingTabIndex ? 0 : -2,
    status: { textContent: '' },
  };
  const source = {
    textContent: text,
    hasAttribute: () => existingTabIndex,
    set tabIndex(value: number) {
      state.tabIndex = value;
    },
    focus: () => {
      state.focused = true;
    },
  } as unknown as HTMLElement;
  const range = {
    selectNodeContents: (node: HTMLElement) => {
      state.selected = node.textContent ?? '';
    },
  };
  vi.stubGlobal(
    'navigator',
    available
      ? {
          clipboard: {
            writeText: async (value: string) => {
              state.clipboard = value;
            },
          },
        }
      : {}
  );
  vi.stubGlobal('document', { createRange: () => range });
  vi.stubGlobal('window', {
    getSelection: () => ({
      removeAllRanges: () => {
        state.selected = '';
      },
      addRange: () => {
        state.selected = source.textContent ?? '';
      },
    }),
  });
  return { state, source, status: state.status as HTMLElement };
}
afterEach(() => vi.unstubAllGlobals());
describe('raw text copy feedback', () => {
  it('copies exact multiline text without injecting action or status labels', async () => {
    const raw = 'lintro check .\n# literal $HOME and <tags>\n';
    const { state, source, status } = environment(raw);
    await copyWithRecovery(source, status);
    expect(state.clipboard).toBe(raw);
    expect(state.status.textContent).toBe('Copied to clipboard.');
    expect(state.focused).toBe(false);
  });
  it('selects the source, focuses recovery and retains instructions after denial', async () => {
    const { state, source, status } = environment('pip install lintro\n');
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText: async () => {
          throw new Error('Denied');
        },
      },
    });
    await copyWithRecovery(source, status);
    expect(state.selected).toBe('pip install lintro\n');
    expect(state.focused).toBe(true);
    expect(state.tabIndex).toBe(-1);
    expect(state.status.textContent).toContain(
      'Text selected; copy with your keyboard or device menu.'
    );
  });
  it('preserves an existing focus target when clipboard support is absent', async () => {
    const { state, source, status } = environment('raw command', false, true);
    await copyWithRecovery(source, status);
    expect(state.selected).toBe('raw command');
    expect(state.tabIndex).toBe(0);
    expect(state.focused).toBe(true);
    expect(status.textContent).toContain('Clipboard unavailable.');
  });
  it('handles empty raw content without a status region', async () => {
    const { state, source } = environment(null);
    await copyWithRecovery(source, null);
    expect(state.clipboard).toBe('');
    expect(state.focused).toBe(false);
  });
  it('keeps manual recovery usable without a browser selection object or status', async () => {
    const { state, source } = environment('manual command', false);
    vi.stubGlobal('window', { getSelection: () => null });
    await copyWithRecovery(source, null);
    expect(state.focused).toBe(true);
    expect(state.tabIndex).toBe(-1);
    expect(source.textContent).toBe('manual command');
  });
});
