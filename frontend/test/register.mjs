/**
 * Registers the `.tsx` test loader (see `tsx-loader.mjs`).
 *
 * Used from the `test` script as `node --import ./test/register.mjs --test …`;
 * it is inert for the `.ts` suites, which are plain pure-function tests.
 */

import { register } from 'node:module'

register('./tsx-loader.mjs', import.meta.url)
