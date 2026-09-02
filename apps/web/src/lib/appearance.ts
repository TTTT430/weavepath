export type Appearance = 'dark' | 'light';

const STORAGE_KEY = 'weavepath.appearance';

export function readAppearance(): Appearance {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    // `soft` was used by the first preview; treat it as the new light theme.
    return value === 'light' || value === 'soft' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

export function applyAppearance(value: Appearance): void {
  document.documentElement.dataset.appearance = value;
}

export function saveAppearance(value: Appearance): void {
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // The visual preference is best-effort in restricted/private browsing.
  }
  applyAppearance(value);
}
