/**
 * Node ESM loader for the `.tsx` unit lane.
 *
 * MatOpt's frontend tests run on `node --test` with Node's own TypeScript
 * stripping, which handles `.ts` but not JSX. This hook adds the two things a
 * React component test needs and nothing else:
 *
 * - `.tsx` files are transformed with the esbuild that already ships in
 *   `node_modules` (Vite's own compiler), JSX → the automatic runtime.
 * - `.css` (including `.module.css`) resolves to a stub whose default export is
 *   a proxy returning the requested class name, so `css.item` in a component
 *   yields a stable string without a CSS pipeline in the test lane.
 *
 * Everything else (`.ts`, `.mjs`, JSON, Node builtins) is left to Node.
 */

import { readFile, access } from 'node:fs/promises'
import { transform } from 'esbuild'

/** Stand-in for a CSS-module import: every class name is its own key. */
const CSS_MODULE_STUB = [
  'const css = new Proxy({}, {',
  "  get: (target, key) => (typeof key === 'string' ? key : undefined),",
  '})',
  'export default css',
  '',
].join('\n')

const sourceRoot = new URL('../src/zcode/', import.meta.url)
export async function resolve(specifier, context, nextResolve) {
  let candidate = specifier.startsWith('@/') ? new URL(specifier.slice(2), sourceRoot)
    : specifier === '@zcode/shared' ? new URL('shared/index.ts', sourceRoot)
    : specifier.startsWith('./') || specifier.startsWith('../') ? new URL(specifier, context.parentURL) : null
  if (candidate?.pathname.endsWith('.js')) {
    for (const extension of ['.ts', '.tsx']) {
      const alternate = new URL(candidate.href.replace(/\.js$/, extension))
      try { await access(alternate); return { url: alternate.href, shortCircuit: true } } catch {}
    }
  }
  if (candidate && specifier.startsWith('@')) return { url: candidate.href, shortCircuit: true }
  return nextResolve(specifier, context)
}

export async function load(url, context, nextLoad) {
  if (url.endsWith('.css')) {
    return { format: 'module', source: CSS_MODULE_STUB, shortCircuit: true }
  }
  if ((url.endsWith('.tsx') || url.endsWith('.ts')) && !url.includes('/node_modules/')) {
    const source = await readFile(new URL(url), 'utf8')
    const { code } = await transform(source, {
      loader: url.endsWith('.tsx') ? 'tsx' : 'ts',
      jsx: 'automatic',
      format: 'esm',
      target: 'es2022',
      sourcefile: url,
    })
    return { format: 'module', source: code, shortCircuit: true }
  }
  return nextLoad(url, context)
}
