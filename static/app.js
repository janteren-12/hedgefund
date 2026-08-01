// For any wide, horizontally-scrollable table (class "table-scroll"), add a
// thin scrollbar directly above it that mirrors the real one. Without this,
// the only scrollbar sits below the table's last row, which can be far off
// screen for a long table.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".table-scroll").forEach((scrollBox) => {
    const table = scrollBox.querySelector("table");
    if (!table) return;

    const topBar = document.createElement("div");
    topBar.className = "table-scroll-top";
    const spacer = document.createElement("div");
    topBar.appendChild(spacer);
    scrollBox.parentNode.insertBefore(topBar, scrollBox);

    const syncSpacerWidth = () => {
      spacer.style.width = table.offsetWidth + "px";
    };
    syncSpacerWidth();
    window.addEventListener("resize", syncSpacerWidth);

    let syncing = false;
    topBar.addEventListener("scroll", () => {
      if (syncing) return;
      syncing = true;
      scrollBox.scrollLeft = topBar.scrollLeft;
      syncing = false;
    });
    scrollBox.addEventListener("scroll", () => {
      if (syncing) return;
      syncing = true;
      topBar.scrollLeft = scrollBox.scrollLeft;
      syncing = false;
    });
  });

  freezeLeadingColumns();
  window.addEventListener("resize", freezeLeadingColumns);
});

// Pins the first few columns of any table.matrix (Ticker, Company, # Funds)
// in place while the rest scrolls horizontally - like Excel's freeze panes.
// Uses each column's actual rendered width (rather than a hardcoded pixel
// value) so it still lines up correctly regardless of content length.
function freezeLeadingColumns(frozenCount = 3) {
  document.querySelectorAll("table.matrix").forEach((table) => {
    const firstRow = table.querySelector("tr");
    if (!firstRow) return;

    const offsets = [];
    let cumulative = 0;
    for (let i = 0; i < frozenCount && i < firstRow.children.length; i++) {
      offsets.push(cumulative);
      cumulative += firstRow.children[i].getBoundingClientRect().width;
    }

    table.querySelectorAll("tr").forEach((row) => {
      for (let i = 0; i < offsets.length && i < row.children.length; i++) {
        const cell = row.children[i];
        cell.classList.add("sticky-col");
        cell.style.left = offsets[i] + "px";
      }
    });
  });
}
