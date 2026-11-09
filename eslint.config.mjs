import globals from "globals";
export default [{
  files: ["app/static/js/*.js"],
  languageOptions: { ecmaVersion: 2022, sourceType: "script", globals: { ...globals.browser, ...globals.serviceworker, bootstrap: "readonly", App: "writable", Html5Qrcode: "readonly", Schlagkarte: "writable" } },
  rules: {
    "no-var": "error", "prefer-const": ["warn", { destructuring: "all" }], "prefer-arrow-callback": ["error", { allowNamedFunctions: true }],
    "object-shorthand": "error", "prefer-template": "off",
    "no-undef": "error", "no-unused-vars": ["warn", { args: "none" }], "no-redeclare": "error", "eqeqeq": ["warn", "smart"],
  },
}];
