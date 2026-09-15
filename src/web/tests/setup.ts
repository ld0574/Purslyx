import { afterEach, beforeEach, vi } from "vitest";

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() { return values.size; },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => { values.delete(key); },
    setItem: (key, value) => { values.set(key, String(value)); },
  };
}

// Node 26 暴露了需要命令行文件参数的实验性 Storage；测试中固定使用浏览器等价的内存实现。
Object.defineProperty(globalThis, "localStorage", { configurable: true, value: memoryStorage() });
Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: memoryStorage() });

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});
