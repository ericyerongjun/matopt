/**
 * Lint for MatOpt's own React code. Vendored ZCode (`src/zcode`) is reviewed
 * through scripts/vendor_check.sh instead and is deliberately out of scope.
 *
 * The Babel parser is used because typescript-eslint does not support the
 * native TypeScript 7 compiler this project builds with; these rules need the
 * syntax tree only, never type information.
 */
import babelParser from "@babel/eslint-parser";
import reactHooks from "eslint-plugin-react-hooks";

const parserFor = (isTSX) => ({
    parser: babelParser,
    parserOptions: {
        requireConfigFile: false,
        babelOptions: {
            babelrc: false,
            configFile: false,
            parserOpts: { plugins: isTSX ? ["typescript", "jsx"] : ["typescript"] },
        },
    },
});

const rules = {
    "react-hooks/rules-of-hooks": "error",
    "react-hooks/exhaustive-deps": "error",
    // The store writes `viseme` every animation frame while speaking, so a
    // whole-store subscription re-renders its component at 60 fps.
    "no-restricted-syntax": ["error", {
        selector: "CallExpression[callee.name='useVoiceStore'][arguments.length=0]",
        message: "Subscribe with a selector; the whole store changes on every speech frame.",
    }],
};

export default [
    { ignores: ["dist/**", "node_modules/**", "src/zcode/**", "public/**"] },
    { files: ["src/**/*.ts"], languageOptions: parserFor(false), plugins: { "react-hooks": reactHooks }, rules },
    { files: ["src/**/*.tsx"], languageOptions: parserFor(true), plugins: { "react-hooks": reactHooks }, rules },
];
