// Ambient declaration for side-effect CSS imports. Vite bundles a `.css`
// import and injects a <link> into the entry HTML; TypeScript needs this stub
// to type-check the import statement. Used by terminal-view.ts for
// `@xterm/xterm/css/xterm.css`.
declare module "*.css";
