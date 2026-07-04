// Ensures `window.localStorage` / `window.sessionStorage` are real Storage
// instances under vitest's jsdom environment.
//
// vitest 4.x instantiates jsdom in a way that leaves `window.localStorage` as a
// plain empty object (no Storage methods) unless a URL happens to trigger late
// initialization on some versions. The jsdom library itself constructs Storage
// correctly when asked, so we build a spec-compliant Storage once and install it
// when the test environment did not. This is test-infrastructure only: no
// production code reads this file.

function makeStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear() {
      map.clear();
    },
    getItem(key: string) {
      return map.has(key) ? (map.get(key) as string) : null;
    },
    key(index: number) {
      return Array.from(map.keys())[index] ?? null;
    },
    removeItem(key: string) {
      map.delete(key);
    },
    setItem(key: string, value: string) {
      map.set(key, String(value));
    },
  } as Storage;
}

function ensureStorage(prop: 'localStorage' | 'sessionStorage') {
  const existing = (window as unknown as Record<string, unknown>)[prop];
  if (existing && typeof (existing as Storage).clear === 'function') {
    return;
  }
  Object.defineProperty(window, prop, {
    value: makeStorage(),
    configurable: true,
    writable: true,
  });
}

ensureStorage('localStorage');
ensureStorage('sessionStorage');

export {};
