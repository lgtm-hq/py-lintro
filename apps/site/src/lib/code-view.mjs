import '../styles/code-view.css';

const preferenceKey = 'lintro-code-view';

function savedView() {
  try {
    return localStorage.getItem(preferenceKey) === 'plain' ? 'plain' : 'syntax';
  } catch {
    return 'syntax';
  }
}

let currentView = savedView();

/**
 * Add an explicit reading choice while retaining the selected palette's syntax.
 * @param {{ pre: HTMLPreElement, example: number }} options
 * @returns {HTMLButtonElement}
 */
export function createCodeViewControl({ pre, example }) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'code-view-toggle';
  button.dataset.codeViewToggle = '';
  button.textContent = 'Plain code';
  button.setAttribute('aria-label', `Plain code for example ${example}`);
  button.setAttribute('aria-pressed', String(currentView === 'plain'));
  button.title = 'Show code without syntax colors';
  pre.dataset.codeView = currentView;
  button.addEventListener('click', () => {
    currentView = currentView === 'plain' ? 'syntax' : 'plain';
    document.querySelectorAll('pre[data-code-view]').forEach((block) => {
      block.setAttribute('data-code-view', currentView);
    });
    document.querySelectorAll('button[data-code-view-toggle]').forEach((control) => {
      control.setAttribute('aria-pressed', String(currentView === 'plain'));
    });
    try {
      localStorage.setItem(preferenceKey, currentView);
    } catch {
      button.title = 'Code view changed for this page; your browser could not save the preference';
    }
  });
  return button;
}
