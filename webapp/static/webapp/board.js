/* Poll only for this account's opaque board revision; never expose application data. */
document.addEventListener("DOMContentLoaded", () => {
  const board = document.getElementById("application-board");
  if (!board) return;
  const current = board.dataset.revision;
  let checking = false;
  async function refreshIfChanged() {
    if (checking || document.hidden || document.querySelector("form:focus-within")) return;
    checking = true;
    try {
      const response = await fetch(board.dataset.revisionUrl, {
        credentials: "same-origin", cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) return;
      const data = await response.json();
      if (data.revision && data.revision !== current) window.location.reload();
    } catch (_) {
      /* Temporary offline state must not interrupt manual edits. */
    } finally {
      checking = false;
    }
  }
  window.setInterval(refreshIfChanged, 4500);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshIfChanged();
  });
});
