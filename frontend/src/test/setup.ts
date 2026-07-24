import "@testing-library/jest-dom/vitest";

// Node 25 内置的 experimental webstorage 会在全局暴露一个缺少方法的 localStorage
// 访问器，遮蔽 jsdom 的实现。此处替换为内存 Storage，保证测试环境行为一致。
class MemoryStorage implements Storage {
  private map = new Map<string, string>();

  get length(): number {
    return this.map.size;
  }

  clear(): void {
    this.map.clear();
  }

  getItem(key: string): string | null {
    return this.map.has(key) ? (this.map.get(key) as string) : null;
  }

  key(index: number): string | null {
    return [...this.map.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.map.delete(key);
  }

  setItem(key: string, value: string): void {
    this.map.set(key, String(value));
  }
}

if (typeof globalThis.localStorage?.setItem !== "function") {
  Object.defineProperty(globalThis, "localStorage", {
    value: new MemoryStorage(),
    configurable: true,
    writable: true,
  });
}
