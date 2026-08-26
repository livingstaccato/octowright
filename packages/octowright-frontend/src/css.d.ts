// Ambient declaration for side-effect CSS imports. Vite bundles a `.css`
// import and injects a <link> into the entry HTML; TypeScript needs this stub
// to type-check the import statement.
declare module "*.css";
