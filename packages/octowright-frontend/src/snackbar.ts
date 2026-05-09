// Toast-style notification used by dashboard action handlers and modals.
// Lives at module scope so a single snackbar element gets reused across
// every call regardless of where in the page tree the trigger came from.

let snackbarEl: HTMLElement | null = null;
let snackbarTimer: ReturnType<typeof setTimeout> | null = null;

function getSnackbar(): HTMLElement {
  if (!snackbarEl) {
    snackbarEl = document.createElement("div");
    snackbarEl.className = "snackbar snackbar--hidden";
    document.body.append(snackbarEl);
  }
  return snackbarEl;
}

export function showSnackbar(msg: string, isError = false): void {
  const el = getSnackbar();
  el.textContent = msg;
  el.className = `snackbar${isError ? " snackbar--error" : ""}`;
  if (snackbarTimer !== null) clearTimeout(snackbarTimer);
  snackbarTimer = setTimeout(() => {
    el.className = "snackbar snackbar--hidden";
    snackbarTimer = null;
  }, 3500);
}
